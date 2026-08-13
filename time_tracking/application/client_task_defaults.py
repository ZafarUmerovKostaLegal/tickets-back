
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.task_billing import (
    BILLING_MODE_FLAT_FEE,
    BILLING_MODE_HOURLY,
    MEHNAT_FLAT_FEE_AMOUNT,
    MEHNAT_FLAT_FEE_CURRENCY,
    MEHNAT_TASK_NAME,
)
from infrastructure.models import TimeManagerClientProjectModel, TimeManagerClientTaskModel
from infrastructure.repositories import ClientTaskRepository


# (name, billable_by_default, billing_mode, flat_fee_amount, flat_fee_currency)
DEFAULT_PROJECT_TASK_SEED: tuple[tuple[str, bool, str, Decimal | None, str | None], ...] = (
    ("Court Hearing", True, BILLING_MODE_HOURLY, None, None),
    ("Court Hearing Preparation", True, BILLING_MODE_HOURLY, None, None),
    ("Document Review", True, BILLING_MODE_HOURLY, None, None),
    ("Document Submission", True, BILLING_MODE_HOURLY, None, None),
    ("Drafting", True, BILLING_MODE_HOURLY, None, None),
    ("Drafting Documents", True, BILLING_MODE_HOURLY, None, None),
    ("Emails", True, BILLING_MODE_HOURLY, None, None),
    ("Meetings", True, BILLING_MODE_HOURLY, None, None),
    (MEHNAT_TASK_NAME, True, BILLING_MODE_FLAT_FEE, MEHNAT_FLAT_FEE_AMOUNT, MEHNAT_FLAT_FEE_CURRENCY),
    ("Research", True, BILLING_MODE_HOURLY, None, None),
    ("Telephone calls", True, BILLING_MODE_HOURLY, None, None),
    ("Kosta Legal Internal", False, BILLING_MODE_HOURLY, None, None),
    ("Accounting", False, BILLING_MODE_HOURLY, None, None),
    ("Business Development", False, BILLING_MODE_HOURLY, None, None),
    ("Lunch/Dinner", False, BILLING_MODE_HOURLY, None, None),
    ("Other research", False, BILLING_MODE_HOURLY, None, None),
    ("Proposals", False, BILLING_MODE_HOURLY, None, None),
    ("Publications", False, BILLING_MODE_HOURLY, None, None),
    ("Review new legislation", False, BILLING_MODE_HOURLY, None, None),
)

_ZERO_RATE = Decimal("0")


async def seed_default_common_tasks_for_project(
    session: AsyncSession,
    project_id: str,
    *,
    only_names: set[str] | None = None,
) -> None:
    """Seed catalog default tasks.

    only_names:
      - None → seed the full default catalog (legacy behavior)
      - set() → seed nothing
      - non-empty set → seed only matching catalog names (case-insensitive)
    """
    r = await session.execute(
        select(TimeManagerClientTaskModel.name).where(TimeManagerClientTaskModel.project_id == project_id)
    )
    existing = {str(x).strip().lower() for x in r.scalars().all()}
    allow = None if only_names is None else {n.strip().lower() for n in only_names if n and str(n).strip()}
    repo = ClientTaskRepository(session)
    for name, billable, billing_mode, flat_amt, flat_cur in DEFAULT_PROJECT_TASK_SEED:
        key = name.strip().lower()
        if allow is not None and key not in allow:
            continue
        if key in existing:
            continue
        await repo.create(
            project_id=project_id,
            name=name.strip(),
            default_billable_rate=_ZERO_RATE,
            billable_by_default=billable,
            billing_mode=billing_mode,
            flat_fee_amount=flat_amt,
            flat_fee_currency=flat_cur,
        )
        existing.add(key)


async def seed_default_tasks_for_all_projects_missing_tasks(session: AsyncSession) -> None:
    r = await session.execute(select(TimeManagerClientProjectModel.id))
    for pid in [str(x) for x in r.scalars().all()]:
        await seed_default_common_tasks_for_project(session, pid)
