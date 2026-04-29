

from __future__ import annotations

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeManagerClientExpenseCategoryModel, TimeManagerClientModel
from infrastructure.repositories import ClientExpenseCategoryRepository
from infrastructure.repository_shared import _now_utc

DEFAULT_EXPENSE_CATEGORY_NAMES: tuple[str, ...] = (
    "Postage expenses",
    "State Fees",
    "Translation and Notarization services",
)


def _names_lower() -> tuple[str, ...]:
    return tuple(n.strip().lower() for n in DEFAULT_EXPENSE_CATEGORY_NAMES)


async def _unarchive_matching_defaults(
    session: AsyncSession,
    *,
    client_id: str | None,
) -> None:

    cond = [
        func.lower(func.trim(TimeManagerClientExpenseCategoryModel.name)).in_(_names_lower()),
        TimeManagerClientExpenseCategoryModel.is_archived.is_(True),
    ]
    if client_id is not None:
        cond.append(TimeManagerClientExpenseCategoryModel.client_id == client_id)
    await session.execute(
        update(TimeManagerClientExpenseCategoryModel)
        .where(and_(*cond))
        .values(is_archived=False, updated_at=_now_utc())
    )


async def _insert_missing_categories(session: AsyncSession, client_id: str) -> None:

    r = await session.execute(
        select(TimeManagerClientExpenseCategoryModel.name).where(
            TimeManagerClientExpenseCategoryModel.client_id == client_id,
        )
    )
    existing = {str(x).strip().lower() for x in r.scalars().all()}
    repo = ClientExpenseCategoryRepository(session)
    for i, name in enumerate(DEFAULT_EXPENSE_CATEGORY_NAMES):
        key = name.strip().lower()
        if key in existing:
            continue
        await repo.create(
            client_id=client_id,
            name=name,
            has_unit_price=False,
            sort_order=i,
        )
        existing.add(key)


async def seed_default_expense_categories_for_client(session: AsyncSession, client_id: str) -> None:

    await _unarchive_matching_defaults(session, client_id=client_id)
    await _insert_missing_categories(session, client_id)


async def seed_default_expense_categories_for_all_clients(session: AsyncSession) -> None:

    await _unarchive_matching_defaults(session, client_id=None)
    r = await session.execute(select(TimeManagerClientModel.id))
    for cid in r.scalars().all():
        await _insert_missing_categories(session, str(cid))
