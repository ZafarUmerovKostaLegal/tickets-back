

from datetime import date
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers
from infrastructure.upstream_http import (
    raise_for_upstream_status,
    send_upstream_request,
    service_base_url,
)


def _base() -> str:
    return service_base_url(get_settings().time_tracking_service_url, "Time tracking")


class TimeEntryCreateBody(BaseModel):


    model_config = ConfigDict(populate_by_name=True)

    work_date: date = Field(..., alias="workDate")
    duration_seconds: Optional[int] = Field(None, alias="durationSeconds", ge=1)
    hours: Optional[Decimal] = None
    is_billable: bool = Field(True, alias="isBillable")
    project_id: Optional[str] = Field(None, alias="projectId")
    task_id: Optional[str] = Field(None, alias="taskId")
    description: Optional[str] = None


class TimeEntryPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    work_date: Optional[date] = Field(None, alias="workDate")
    duration_seconds: Optional[int] = Field(None, alias="durationSeconds", ge=1)
    hours: Optional[Decimal] = None
    is_billable: Optional[bool] = Field(None, alias="isBillable")
    project_id: Optional[str] = Field(None, alias="projectId")
    task_id: Optional[str] = Field(None, alias="taskId")
    description: Optional[str] = None


class TimeEntryDeleteBody(BaseModel):


    model_config = ConfigDict(populate_by_name=True)

    void_kind: Literal["rejected", "reallocated"] = Field("rejected", alias="voidKind")


async def time_entries_list_gateway(auth_user_id: int, request: Request) -> Any:
    base = _base()
    r = await send_upstream_request(
        "GET",
        f"{base}/users/{auth_user_id}/time-entries",
        params=request.query_params,
        headers=merge_upstream_headers(),
        timeout=15.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()


async def time_entries_create_gateway(auth_user_id: int, body: TimeEntryCreateBody) -> Any:
    base = _base()
    r = await send_upstream_request(
        "POST",
        f"{base}/users/{auth_user_id}/time-entries",
        json=body.model_dump(mode="json", by_alias=False),
        headers=merge_upstream_headers(),
        timeout=15.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()


async def time_entries_patch_gateway(auth_user_id: int, entry_id: str, body: TimeEntryPatchBody) -> Any:
    base = _base()
    payload = body.model_dump(exclude_unset=True, mode="json", by_alias=False)
    if not payload:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    r = await send_upstream_request(
        "PATCH",
        f"{base}/users/{auth_user_id}/time-entries/{entry_id}",
        json=payload,
        headers=merge_upstream_headers(),
        timeout=15.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()


async def time_entries_delete_gateway(
    auth_user_id: int,
    entry_id: str,
    body: TimeEntryDeleteBody | None = None,
) -> dict | None:
    base = _base()
    kwargs: dict[str, Any] = {"headers": merge_upstream_headers()}
    if body is not None:
        kwargs["json"] = body.model_dump(mode="json", by_alias=True, exclude_unset=True)
    r = await send_upstream_request(
        "DELETE",
        f"{base}/users/{auth_user_id}/time-entries/{entry_id}",
        timeout=15.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
        **kwargs,
    )
    raise_for_upstream_status(r, "Time tracking service error")
    if r.status_code == 204:
        return None
    if (r.text or "").strip():
        return r.json()
    return None


class ProjectAccessPutBody(BaseModel):


    model_config = ConfigDict(populate_by_name=True)

    project_ids: list[str] = Field(default_factory=list, alias="projectIds")


async def project_access_get_gateway(auth_user_id: int) -> Any:
    base = _base()
    r = await send_upstream_request(
        "GET",
        f"{base}/users/{auth_user_id}/project-access",
        headers=merge_upstream_headers(),
        timeout=15.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()


async def project_access_put_gateway(
    auth_user_id: int,
    body: ProjectAccessPutBody,
    *,
    granted_by_auth_user_id: int,
) -> Any:
    base = _base()
    payload = {
        "project_ids": list(body.project_ids),
        "granted_by_auth_user_id": granted_by_auth_user_id,
    }
    r = await send_upstream_request(
        "PUT",
        f"{base}/users/{auth_user_id}/project-access",
        json=payload,
        headers=merge_upstream_headers(),
        timeout=15.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()
