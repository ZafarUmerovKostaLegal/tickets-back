

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeManagerClientProjectModel, TimeManagerClientTaskModel
from infrastructure.repositories import ClientTaskRepository


DEFAULT_COMMON_TASK_NAMES: tuple[str, ...] = (
    "Accounting",
    "Business Development",
    "Court Hearing",
    "Court Hearing Preparation",
    "Drafting Documents",
    "Lunch/Dinner",
    "Other research",
    "Proposals",
    "Publications",
    "Review new legislation",
)


def _names_lower() -> tuple[str, ...]:
    return tuple(n.strip().lower() for n in DEFAULT_COMMON_TASK_NAMES)


async def seed_default_common_tasks_for_project(session: AsyncSession, project_id: str) -> None:
    r = await session.execute(
        select(TimeManagerClientTaskModel.name).where(TimeManagerClientTaskModel.project_id == project_id)
    )
    existing = {str(x).strip().lower() for x in r.scalars().all()}
    repo = ClientTaskRepository(session)
    for name in DEFAULT_COMMON_TASK_NAMES:
        if name.strip().lower() in existing:
            continue
        await repo.create(
            project_id=project_id,
            name=name,
            default_billable_rate=None,
            billable_by_default=True,
        )
        existing.add(name.strip().lower())


async def seed_default_tasks_for_all_projects_missing_tasks(session: AsyncSession) -> None:
    r = await session.execute(select(TimeManagerClientProjectModel.id))
    pids = [str(x) for x in r.scalars().all()]
    for pid in pids:
        qc = await session.execute(
            select(func.count()).select_from(TimeManagerClientTaskModel).where(
                TimeManagerClientTaskModel.project_id == pid
            )
        )
        if int(qc.scalar_one() or 0) == 0:
            await seed_default_common_tasks_for_project(session, pid)
