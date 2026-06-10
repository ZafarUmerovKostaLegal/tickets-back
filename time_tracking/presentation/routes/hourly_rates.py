

from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import ensure_time_entry_subject_allowed
from infrastructure.database import get_session
from infrastructure.repositories import (
    ClientProjectRepository,
    HourlyRateRepository,
    TimeTrackingUserRepository,
)
from presentation.deps import require_bearer_user
from presentation.schemas import (
    HourlyRateChangeFromBody,
    HourlyRateChangeFromResult,
    HourlyRateCreateBody,
    HourlyRateOut,
    HourlyRatePatchBody,
)

router = APIRouter(prefix="/users", tags=["hourly_rates"])


class RateKindQuery(str, Enum):
    billable = "billable"
    cost = "cost"


async def _ensure_user(session: AsyncSession, auth_user_id: int) -> None:
    ur = TimeTrackingUserRepository(session)
    if not await ur.get_by_auth_user_id(auth_user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")


@router.get("/{auth_user_id}/hourly-rates/{rate_id}", response_model=HourlyRateOut)
async def get_hourly_rate(
    auth_user_id: int,
    rate_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> HourlyRateOut:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=False)
    await _ensure_user(session, auth_user_id)
    repo = HourlyRateRepository(session)
    row = await repo.get_by_id(auth_user_id, rate_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ставка не найдена")
    return HourlyRateOut.model_validate(row)


@router.get("/{auth_user_id}/hourly-rates", response_model=list[HourlyRateOut])
async def list_hourly_rates(
    auth_user_id: int,
    kind: RateKindQuery = Query(..., alias="kind"),
    project_id: str | None = Query(
        None,
        alias="projectId",
        description="Фильтр по проекту: ставки только этого проекта. 'global' — только общие (без привязки).",
    ),
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> list[HourlyRateOut]:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=False)
    ur = TimeTrackingUserRepository(session)
    if not await ur.get_by_auth_user_id(auth_user_id):
        return []
    repo = HourlyRateRepository(session)
    rows = await repo.list_by_user_and_kind(auth_user_id, kind.value)
    pid = (project_id or "").strip()
    if pid.lower() == "global":
        rows = [r for r in rows if not (getattr(r, "applies_to_project_id", None) or None)]
    elif pid:
        rows = [r for r in rows if (getattr(r, "applies_to_project_id", None) or None) == pid]
    return [HourlyRateOut.model_validate(r) for r in rows]


@router.post("/{auth_user_id}/hourly-rates", response_model=HourlyRateOut)
async def create_hourly_rate(
    auth_user_id: int,
    body: HourlyRateCreateBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> HourlyRateOut:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, auth_user_id)
    project_id = (body.applies_to_project_id or "").strip() or None
    if project_id is not None:
        projects = ClientProjectRepository(session)
        if await projects.get_by_id_global(project_id) is None:
            raise HTTPException(status_code=404, detail="Проект не найден")
    repo = HourlyRateRepository(session)
    try:
        row = await repo.create(
            auth_user_id=auth_user_id,
            rate_kind=body.rate_kind.value,
            amount=body.amount,
            currency=body.currency,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            applies_to_project_id=project_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    await session.refresh(row)
    return HourlyRateOut.model_validate(row)


@router.post("/{auth_user_id}/hourly-rates/change-from", response_model=HourlyRateChangeFromResult)
async def change_hourly_rate_from(
    auth_user_id: int,
    body: HourlyRateChangeFromBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> HourlyRateChangeFromResult:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, auth_user_id)
    project_id = (body.applies_to_project_id or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="Не указан проект для смены ставки")
    projects = ClientProjectRepository(session)
    if await projects.get_by_id_global(project_id) is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    repo = HourlyRateRepository(session)
    try:
        result = await repo.change_project_rate_from(
            auth_user_id=auth_user_id,
            rate_kind=body.rate_kind.value,
            amount=body.amount,
            currency=body.currency,
            applies_to_project_id=project_id,
            effective_from=body.effective_from,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail="Ставка не найдена") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    for row in result.values():
        if row is not None:
            await session.refresh(row)
    return HourlyRateChangeFromResult(
        new_rate=HourlyRateOut.model_validate(result["new"]),
        closed_rate=HourlyRateOut.model_validate(result["closed"]) if result["closed"] else None,
        before_rate=HourlyRateOut.model_validate(result["before"]) if result["before"] else None,
        updated_rate=HourlyRateOut.model_validate(result["updated"]) if result["updated"] else None,
    )


@router.patch("/{auth_user_id}/hourly-rates/{rate_id}", response_model=HourlyRateOut)
async def patch_hourly_rate(
    auth_user_id: int,
    rate_id: str,
    body: HourlyRatePatchBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> HourlyRateOut:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, auth_user_id)
    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    repo = HourlyRateRepository(session)
    try:
        row = await repo.update(auth_user_id=auth_user_id, rate_id=rate_id, patch=patch)
    except LookupError as e:
        if str(e) == "not_found":
            raise HTTPException(status_code=404, detail="Ставка не найдена") from e
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    await session.refresh(row)
    return HourlyRateOut.model_validate(row)


@router.delete("/{auth_user_id}/hourly-rates/{rate_id}")
async def delete_hourly_rate(
    auth_user_id: int,
    rate_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> dict:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, auth_user_id)
    repo = HourlyRateRepository(session)
    ok = await repo.delete(auth_user_id, rate_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Ставка не найдена")
    await session.commit()
    return {"ok": True}
