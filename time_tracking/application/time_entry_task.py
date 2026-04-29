

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.repositories import ClientProjectRepository, ClientTaskRepository


async def resolve_time_entry_task_for_project(
    session: AsyncSession,
    project_id: str | None,
    task_id: str | None,
) -> tuple[str | None, bool | None]:

    if task_id is None:
        return None, None
    raw = str(task_id).strip()
    if not raw:
        return None, None
    if project_id is None or not str(project_id).strip():
        raise HTTPException(
            status_code=400,
            detail="Чтобы указать задачу, выберите проект",
        )
    pid = str(project_id).strip()
    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(pid)
    if not proj:
        raise HTTPException(status_code=400, detail="Проект не найден")
    ctr = ClientTaskRepository(session)
    task = await ctr.get_by_id(proj.client_id, raw)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Задача не найдена или не относится к клиенту этого проекта",
        )
    return raw, bool(task.billable_by_default)
