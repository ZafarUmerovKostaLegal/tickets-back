

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from infrastructure.database import get_session
from infrastructure.repositories import ClientTaskRepository
from presentation.routes.client_access import ensure_client_not_archived, get_client_or_404
from presentation.schemas import (
    TimeManagerClientTaskCreateBody,
    TimeManagerClientTaskOut,
    TimeManagerClientTaskPatchBody,
)

router = APIRouter(prefix="/clients", tags=["client_tasks"])


def _task_out(row) -> TimeManagerClientTaskOut:
    return TimeManagerClientTaskOut.model_validate(row)


async def _require_client(session: AsyncSession, client_id: str) -> None:
    await get_client_or_404(session, client_id)


async def _require_client_mutable(session: AsyncSession, client_id: str) -> None:
    row = await get_client_or_404(session, client_id)
    ensure_client_not_archived(row)


@router.get("/{client_id}/tasks", response_model=list[TimeManagerClientTaskOut])
async def list_client_tasks(client_id: str, session: AsyncSession = Depends(get_session)):
    await _require_client(session, client_id)
    repo = ClientTaskRepository(session)
    rows = await repo.list_for_client(client_id)
    return [_task_out(r) for r in rows]


@router.get("/{client_id}/tasks/{task_id}", response_model=TimeManagerClientTaskOut)
async def get_client_task(
    client_id: str,
    task_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client(session, client_id)
    repo = ClientTaskRepository(session)
    row = await repo.get_by_id(client_id, task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_out(row)


@router.post("/{client_id}/tasks", response_model=TimeManagerClientTaskOut)
async def create_client_task(
    client_id: str,
    body: TimeManagerClientTaskCreateBody,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientTaskRepository(session)
    row = await repo.create(
        client_id=client_id,
        name=body.name,
        default_billable_rate=body.default_billable_rate,
        billable_by_default=body.billable_by_default,
        common_for_future_projects=body.common_for_future_projects,
        add_to_existing_projects=body.add_to_existing_projects,
    )
    await session.commit()
    return _task_out(row)


@router.patch("/{client_id}/tasks/{task_id}", response_model=TimeManagerClientTaskOut)
async def patch_client_task(
    client_id: str,
    task_id: str,
    body: TimeManagerClientTaskPatchBody,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientTaskRepository(session)
    patch = body.model_dump(exclude_unset=True, mode="json", by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    row = await repo.update(client_id, task_id, patch)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    await session.commit()
    return _task_out(row)


@router.delete("/{client_id}/tasks/{task_id}", status_code=204)
async def delete_client_task(
    client_id: str,
    task_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientTaskRepository(session)
    ok = await repo.delete(client_id, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    await session.commit()
    return Response(status_code=204)
