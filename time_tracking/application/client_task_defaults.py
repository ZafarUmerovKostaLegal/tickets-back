

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeManagerClientProjectModel, TimeManagerClientTaskModel
from infrastructure.repositories import ClientTaskRepository


DEFAULT_PROJECT_TASK_SEED: tuple[tuple[str, bool], ...] = (
    ("Court Hearing", True),
    ("Court Hearing Preparation", True),
    ("Document Review", True),
    ("Document Submission", True),
    ("Drafting", True),
    ("Drafting Documents", True),
    ("Emails", True),
    ("Meetings", True),
    ("My mehnat registration", True),
    ("Research", True),
    ("Telephone calls", True),
    ("Kosta Legal Internal", False),
    ("Accounting", False),
    ("Business Development", False),
    ("Lunch/Dinner", False),
    ("Other research", False),
    ("Proposals", False),
    ("Publications", False),
    ("Review new legislation", False),
)

_ZERO_RATE = Decimal("0")


async def seed_default_common_tasks_for_project(session: AsyncSession, project_id: str) -> None:
    r = await session.execute(
        select(TimeManagerClientTaskModel.name).where(TimeManagerClientTaskModel.project_id == project_id)
    )
    existing = {str(x).strip().lower() for x in r.scalars().all()}
    repo = ClientTaskRepository(session)
    for name, billable in DEFAULT_PROJECT_TASK_SEED:
        key = name.strip().lower()
        if key in existing:
            continue
        await repo.create(
            project_id=project_id,
            name=name.strip(),
            default_billable_rate=_ZERO_RATE,
            billable_by_default=billable,
        )
        existing.add(key)


async def seed_default_tasks_for_all_projects_missing_tasks(session: AsyncSession) -> None:
    r = await session.execute(select(TimeManagerClientProjectModel.id))
    for pid in [str(x) for x in r.scalars().all()]:
        await seed_default_common_tasks_for_project(session, pid)
