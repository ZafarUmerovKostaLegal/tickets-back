

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings

_log = logging.getLogger(__name__)


async def fetch_auth_user_positions_by_id(authorization: str) -> dict[int, str | None]:

    authz = (authorization or "").strip()
    if not authz:
        return {}
    base = (get_settings().auth_service_url or "").strip().rstrip("/")
    if not base:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{base}/users",
                params={"include_archived": "true"},
                headers={"Authorization": authz},
            )
    except httpx.RequestError as e:
        _log.debug("auth users list for position merge: %s", e)
        return {}
    if r.status_code != 200:
        _log.debug("auth users list: HTTP %s", r.status_code)
        return {}
    data = r.json()
    if not isinstance(data, list):
        return {}
    out: dict[int, str | None] = {}
    for u in data:
        try:
            uid = u.get("id")
            if uid is None:
                continue
            out[int(uid)] = u.get("position")
        except (TypeError, ValueError):
            continue
    return out


async def fetch_auth_user_partner_hints_by_id(
    authorization: str,
) -> dict[int, dict[str, str | None]]:

    authz = (authorization or "").strip()
    if not authz:
        return {}
    base = (get_settings().auth_service_url or "").strip().rstrip("/")
    if not base:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{base}/users",
                params={"include_archived": "true"},
                headers={"Authorization": authz},
            )
    except httpx.RequestError as e:
        _log.debug("auth users list for partner hints: %s", e)
        return {}
    if r.status_code != 200:
        _log.debug("auth users list for partner hints: HTTP %s", r.status_code)
        return {}
    data = r.json()
    if not isinstance(data, list):
        return {}
    out: dict[int, dict[str, str | None]] = {}
    for u in data:
        try:
            uid = u.get("id")
            if uid is None:
                continue
            i = int(uid)
        except (TypeError, ValueError):
            continue
        pos = u.get("position")
        pos_s = (str(pos).strip() if pos is not None and str(pos).strip() else None)
        role = u.get("role")
        role_s = (str(role).strip() if role is not None and str(role).strip() else None)
        out[i] = {"position": pos_s, "role": role_s}
    return out


async def fetch_auth_user_detail(authorization: str, auth_user_id: int) -> dict | None:

    authz = (authorization or "").strip()
    if not authz:
        return None
    base = (get_settings().auth_service_url or "").strip().rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{base}/users/{int(auth_user_id)}",
                headers={"Authorization": authz},
            )
    except httpx.RequestError as e:
        _log.debug("auth user %s detail: %s", auth_user_id, e)
        return None
    if r.status_code != 200:
        _log.debug("auth user %s detail: HTTP %s", auth_user_id, r.status_code)
        return None
    data = r.json()
    return data if isinstance(data, dict) else None


async def fetch_auth_user_position(authorization: str, auth_user_id: int) -> str | None:

    data = await fetch_auth_user_detail(authorization, auth_user_id)
    if not data:
        return None
    pos = data.get("position")
    if pos is None:
        return None
    s = str(pos).strip()
    return s if s else None


async def ensure_time_tracking_user_from_auth(
    session: AsyncSession,
    authorization: str | None,
    auth_user_id: int,
) -> None:

    from infrastructure.repositories import TimeTrackingUserRepository

    tur = TimeTrackingUserRepository(session)
    if await tur.get_by_auth_user_id(auth_user_id):
        return
    authz = (authorization or "").strip()
    if not authz:
        raise ValueError(
            "Нужен заголовок Authorization, чтобы добавить пользователя в учёт времени из auth."
        )
    detail = await fetch_auth_user_detail(authz, auth_user_id)
    if not detail:
        raise ValueError(
            f"Не удалось загрузить пользователя id={auth_user_id} из auth (проверьте токен и права на просмотр профиля)."
        )
    email = (detail.get("email") or "").strip()
    if not email:
        raise ValueError(f"У пользователя id={auth_user_id} в auth нет email — запись в учёт времени невозможна.")
    tt_role = (
        (detail.get("time_tracking_role") or detail.get("timeTrackingRole") or "") or ""
    ).strip()
    pos = detail.get("position")
    pos_s = str(pos).strip() if pos is not None and str(pos).strip() else None
    await tur.upsert_user(
        auth_user_id=auth_user_id,
        email=email,
        display_name=detail.get("display_name"),
        picture=detail.get("picture"),
        role=tt_role,
        is_blocked=bool(detail.get("is_blocked", False)),
        is_archived=bool(detail.get("is_archived", False)),
        weekly_capacity_hours=None,
        position=pos_s,
        update_position=True,
    )
