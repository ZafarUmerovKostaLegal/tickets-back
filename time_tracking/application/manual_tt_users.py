from __future__ import annotations

import re
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

MANUAL_TT_USER_AUTH_ID_FLOOR = 2_000_000_000
MANUAL_TT_USER_EMAIL_DOMAIN = "manual.kostalegal.local"


def is_manual_tt_auth_user_id(auth_user_id: int) -> bool:
    return int(auth_user_id) >= MANUAL_TT_USER_AUTH_ID_FLOOR


def slugify_manual_tt_local_part(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", (display_name or "").strip().lower())
    return slug.strip(".") or "user"


def build_manual_tt_email(display_name: str, *, unique_suffix: str | None = None) -> str:
    local = f"manual.{slugify_manual_tt_local_part(display_name)}"
    if unique_suffix:
        local = f"{local}.{unique_suffix}"
    max_local = 255 - len(MANUAL_TT_USER_EMAIL_DOMAIN) - 1
    if len(local) > max_local:
        local = local[:max_local]
    return f"{local}@{MANUAL_TT_USER_EMAIL_DOMAIN}"


async def allocate_manual_auth_user_id(session: AsyncSession) -> int:
    from infrastructure.models import TimeTrackingUserModel

    r = await session.execute(select(func.max(TimeTrackingUserModel.auth_user_id)))
    mx = int(r.scalar_one_or_none() or 0)
    return max(MANUAL_TT_USER_AUTH_ID_FLOOR, mx + 1)


async def create_manual_tt_user(
    session: AsyncSession,
    *,
    display_name: str,
    email: str | None = None,
    position: str | None = None,
    is_archived: bool = True,
    weekly_capacity_hours: Decimal | None = None,
) -> TimeTrackingUserModel:
    from infrastructure.models import TimeTrackingUserModel
    from infrastructure.repositories import TimeTrackingUserRepository

    name = (display_name or "").strip()
    if not name:
        raise ValueError("Укажите имя сотрудника")

    repo = TimeTrackingUserRepository(session)
    normalized_email = (email or "").strip().lower() or None
    if normalized_email:
        existing = await repo.get_by_email(normalized_email)
        if existing is not None:
            raise ValueError("Пользователь с таким email уже есть в учёте времени")
        final_email = normalized_email
    else:
        final_email = build_manual_tt_email(name)
        if await repo.get_by_email(final_email) is not None:
            final_email = build_manual_tt_email(name, unique_suffix=uuid.uuid4().hex[:8])

    auth_user_id = await allocate_manual_auth_user_id(session)
    pos = (position or "").strip() or None
    return await repo.upsert_user(
        auth_user_id=auth_user_id,
        email=final_email,
        display_name=name,
        picture=None,
        role="",
        is_blocked=False,
        is_archived=is_archived,
        weekly_capacity_hours=weekly_capacity_hours,
        position=pos,
        update_position=True,
    )
