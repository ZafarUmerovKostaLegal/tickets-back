

import uuid
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import (
    ensure_time_entry_subject_allowed,
    viewer_can_bypass_work_week_submission_lock,
)
from application.time_entry_task import resolve_time_entry_task_for_project
from application.weekly_submission_service import is_work_date_locked_for_user
from infrastructure.database import get_session
from infrastructure.repositories import (
    ClientProjectRepository,
    TimeEntryRepository,
    TimeTrackingUserRepository,
    UserProjectAccessRepository,
)
from infrastructure.repository_invoices import InvoiceRepository
from presentation.deps import require_bearer_user
from presentation.schemas import (
    TimeEntryCreateBody,
    TimeEntryDeleteBody,
    TimeEntryOut,
    TimeEntryPatchBody,
)

router = APIRouter(prefix="/users", tags=["time_entries"])


async def _ensure_user(session: AsyncSession, auth_user_id: int) -> None:
    ur = TimeTrackingUserRepository(session)
    if not await ur.get_by_auth_user_id(auth_user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")


def _normalize_project_id(project_id: str | None) -> str | None:
    if project_id is None:
        return None
    s = str(project_id).strip()
    return s if s else None


async def _validate_project_if_set(
    session: AsyncSession,
    project_id: str | None,
) -> str | None:

    pid = _normalize_project_id(project_id)
    if pid is None:
        return None
    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(pid)
    if not proj:
        raise HTTPException(status_code=400, detail="Проект не найден")
    if proj.is_archived:
        raise HTTPException(status_code=400, detail="Проект в архиве, списание времени недоступно")
    return pid


async def _raise_if_work_date_is_closed(
    session: AsyncSession,
    viewer: dict,
    auth_user_id: int,
    work_date: date,
    *,
    detail: str,
) -> None:
    if await viewer_can_bypass_work_week_submission_lock(session, viewer):
        return
    if await is_work_date_locked_for_user(session, auth_user_id, work_date):
        raise HTTPException(status_code=409, detail=detail)


async def _require_project_access_if_set(
    session: AsyncSession,
    auth_user_id: int,
    project_id: str | None,
) -> None:
    if project_id is None:
        return
    pid = str(project_id).strip()
    if not pid:
        return
    par = UserProjectAccessRepository(session)
    if not await par.has_access(auth_user_id, pid):
        raise HTTPException(
            status_code=403,
            detail="Нет доступа к этому проекту для списания времени",
        )


@router.get("/{auth_user_id}/time-entries", response_model=list[TimeEntryOut])
async def list_time_entries(
    auth_user_id: int,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> list[TimeEntryOut]:

    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=False)
    await _ensure_user(session, auth_user_id)
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="Параметр to не может быть раньше from")
    repo = TimeEntryRepository(session)
    rows = await repo.list_for_user(auth_user_id, date_from, date_to)
    return [TimeEntryOut.model_validate(r) for r in rows]


@router.post("/{auth_user_id}/time-entries", response_model=TimeEntryOut)
async def create_time_entry(
    auth_user_id: int,
    body: TimeEntryCreateBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> TimeEntryOut:

    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, auth_user_id)
    await _raise_if_work_date_is_closed(
        session,
        viewer,
        auth_user_id,
        body.work_date,
        detail="Период уже сдан. Редактирование даты запрещено (обратитесь к менеджеру).",
    )
    project_id = await _validate_project_if_set(session, body.project_id)
    await _require_project_access_if_set(session, auth_user_id, project_id)
    tid, bb = await resolve_time_entry_task_for_project(session, project_id, body.task_id)
    is_billable = body.is_billable if tid is None else bool(bb)
    repo = TimeEntryRepository(session)
    try:
        row = await repo.create(
            entry_id=str(uuid.uuid4()),
            auth_user_id=auth_user_id,
            work_date=body.work_date,
            duration_seconds=body.duration_seconds,
            hours=body.hours,
            is_billable=is_billable,
            project_id=project_id,
            task_id=tid,
            description=body.description,
            external_reference_url=body.external_reference_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    await session.refresh(row)
    return TimeEntryOut.model_validate(row)


@router.patch("/{auth_user_id}/time-entries/{entry_id}", response_model=TimeEntryOut)
async def patch_time_entry(
    auth_user_id: int,
    entry_id: str,
    body: TimeEntryPatchBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> TimeEntryOut:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, auth_user_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    repo = TimeEntryRepository(session)
    row = await repo.get_by_id(auth_user_id, entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.voided_at is not None:
        raise HTTPException(
            status_code=400,
            detail="Запись снята с учёта менеджером и не может быть изменена",
        )
    await _raise_if_work_date_is_closed(
        session,
        viewer,
        auth_user_id,
        row.work_date,
        detail="Период уже сдан. Редактирование запрещено (обратитесь к менеджеру).",
    )
    if patch.get("work_date"):
        await _raise_if_work_date_is_closed(
            session,
            viewer,
            auth_user_id,
            patch["work_date"],
            detail="Целевой день в закрытом периоде. Перенос запрещён.",
        )

    row_norm_project_id = _normalize_project_id(
        str(row.project_id) if row.project_id is not None else None
    )
    project_changed_clears_task = False
    if "project_id" in patch:
        project_id = await _validate_project_if_set(session, patch.get("project_id"))
        patch["project_id"] = project_id
        await _require_project_access_if_set(session, auth_user_id, project_id)
        new_norm = _normalize_project_id(
            str(project_id) if project_id is not None else None
        )
        if new_norm != row_norm_project_id and "task_id" not in patch:
            project_changed_clears_task = True

    eff_proj = patch["project_id"] if "project_id" in patch else row.project_id
    if project_changed_clears_task:
        eff_task: str | None = None
    else:
        eff_task = patch["task_id"] if "task_id" in patch else row.task_id
    tid, bb = await resolve_time_entry_task_for_project(session, eff_proj, eff_task)
    if tid is not None:
        patch["task_id"] = tid
        patch["is_billable"] = bb
    elif "task_id" in patch or project_changed_clears_task:
        patch["task_id"] = None

    try:
        row = await repo.update(auth_user_id=auth_user_id, entry_id=entry_id, patch=patch)
    except LookupError as e:
        if str(e) == "not_found":
            raise HTTPException(status_code=404, detail="Запись не найдена") from e
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    await session.refresh(row)
    return TimeEntryOut.model_validate(row)


@router.delete(
    "/{auth_user_id}/time-entries/{entry_id}",
    response_model=None,
    responses={
        204: {"description": "Собственная запись физически удалена"},
        200: {
            "description": "Снятие с учёта (менеджер/админ). Строка остаётся в БД, сотруднику видна как void",
            "model": TimeEntryOut,
        },
        409: {"description": "Запись в неотменённом счёте"},
    },
)
async def delete_time_entry(
    auth_user_id: int,
    entry_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    body: TimeEntryDeleteBody | None = Body(default=None),
):

    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, auth_user_id)
    repo = TimeEntryRepository(session)
    row = await repo.get_by_id(auth_user_id, entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.voided_at is not None:
        raise HTTPException(
            status_code=400,
            detail="Запись уже снята с учёта",
        )
    v_id = (viewer or {}).get("id")
    if v_id is None:
        raise HTTPException(status_code=403, detail="В токене нет id пользователя")
    viewer_id = int(v_id)
    is_self = viewer_id == auth_user_id

    inv = InvoiceRepository(session)
    on_inv = await inv.time_entry_on_active_invoice(entry_id)
    if on_inv:
        raise HTTPException(
            status_code=409,
            detail="Запись включена в счёт. Снятие с учёта или удаление недоступны.",
        )
    await _raise_if_work_date_is_closed(
        session,
        viewer,
        auth_user_id,
        row.work_date,
        detail="Период уже сдан. Удаление запрещено (обратитесь к менеджеру).",
    )
    if is_self:
        ok = await repo.delete(auth_user_id, entry_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    void_kind = (body.void_kind if body is not None else "rejected")
    try:
        row2 = await repo.void_entry(
            auth_user_id,
            entry_id,
            voided_by_auth_user_id=viewer_id,
            void_kind=void_kind,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LookupError:
        raise HTTPException(status_code=404, detail="Запись не найдена") from None
    await session.commit()
    await session.refresh(row2)
    return TimeEntryOut.model_validate(row2)
