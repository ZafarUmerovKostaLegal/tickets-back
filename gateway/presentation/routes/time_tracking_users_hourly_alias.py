

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from starlette.responses import Response

from presentation.routes.time_tracking_hourly_proxy import (
    HourlyRateCreateBody,
    HourlyRatePatchBody,
    get_current_user,
    hourly_rates_create_gateway,
    hourly_rates_delete_gateway,
    hourly_rates_get_gateway,
    hourly_rates_list_gateway,
    hourly_rates_patch_gateway,
)
from presentation.routes.time_tracking_routes import (
    require_manage_project_access,
    require_manage_role,
    require_time_entry_read,
    require_time_entry_write,
    require_view_project_access,
    require_view_role,
)
from presentation.routes.time_tracking_te_proxy import (
    ProjectAccessPutBody,
    TimeEntryCreateBody,
    TimeEntryDeleteBody,
    TimeEntryPatchBody,
    project_access_get_gateway,
    project_access_put_gateway,
    time_entries_create_gateway,
    time_entries_delete_gateway,
    time_entries_list_gateway,
    time_entries_patch_gateway,
)

router = APIRouter(prefix="/api/v1/users", tags=["time_tracking"])


@router.get("/{user_id}/hourly-rates")
async def list_hourly_rates_under_users(
    user_id: int,
    kind: str = Query(..., description="billable | cost"),
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_list_gateway(user_id, kind, user)


@router.get("/{user_id}/hourly-rates/{rate_id}")
async def get_hourly_rate_under_users(
    user_id: int,
    rate_id: str,
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_get_gateway(user_id, rate_id, user)


@router.post("/{user_id}/hourly-rates")
async def create_hourly_rate_under_users(
    user_id: int,
    body: HourlyRateCreateBody,
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_create_gateway(user_id, body, user)


@router.patch("/{user_id}/hourly-rates/{rate_id}")
async def patch_hourly_rate_under_users(
    user_id: int,
    rate_id: str,
    body: HourlyRatePatchBody,
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_patch_gateway(user_id, rate_id, body, user)


@router.delete("/{user_id}/hourly-rates/{rate_id}")
async def delete_hourly_rate_under_users(
    user_id: int,
    rate_id: str,
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_delete_gateway(user_id, rate_id, user)


@router.get("/{auth_user_id}/time-entries")
async def list_time_entries_under_users(
    auth_user_id: int,
    request: Request,
    _: dict = Depends(require_time_entry_read),
):
    return await time_entries_list_gateway(auth_user_id, request)


@router.post("/{auth_user_id}/time-entries")
async def create_time_entry_under_users(
    auth_user_id: int,
    body: TimeEntryCreateBody,
    _: dict = Depends(require_time_entry_write),
):
    return await time_entries_create_gateway(auth_user_id, body)


@router.patch("/{auth_user_id}/time-entries/{entry_id}")
async def patch_time_entry_under_users(
    auth_user_id: int,
    entry_id: str,
    body: TimeEntryPatchBody,
    _: dict = Depends(require_time_entry_write),
):
    return await time_entries_patch_gateway(auth_user_id, entry_id, body)


@router.delete(
    "/{auth_user_id}/time-entries/{entry_id}",
    responses={204: {"description": "Собственная запись удалена"}, 200: {"description": "Снятие с учёта"}},
)
async def delete_time_entry_under_users(
    auth_user_id: int,
    entry_id: str,
    body: TimeEntryDeleteBody | None = Body(None),
    _: dict = Depends(require_time_entry_write),
):
    out = await time_entries_delete_gateway(auth_user_id, entry_id, body)
    if out is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return out


@router.get("/{auth_user_id}/project-access")
async def get_project_access_under_users(
    auth_user_id: int,
    _: dict = Depends(require_view_project_access),
):
    return await project_access_get_gateway(auth_user_id)


@router.put("/{auth_user_id}/project-access")
async def put_project_access_under_users(
    auth_user_id: int,
    body: ProjectAccessPutBody,
    user: dict = Depends(require_manage_project_access),
):
    uid = user.get("id")
    if uid is None:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return await project_access_put_gateway(
        auth_user_id,
        body,
        granted_by_auth_user_id=int(uid),
    )
