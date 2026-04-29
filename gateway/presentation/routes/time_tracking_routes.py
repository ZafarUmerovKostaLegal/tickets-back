

import json
from decimal import Decimal
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from starlette.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers
from infrastructure.upstream_http import (
    raise_for_upstream_status,
    send_upstream_request,
    service_base_url,
)
from presentation.schemas.time_manager_client_contacts import (
    TimeManagerClientContactCreateBody,
    TimeManagerClientContactPatchBody,
)
from presentation.schemas.time_manager_clients import (
    TimeManagerClientCreateBody,
    TimeManagerClientPatchBody,
)
from presentation.schemas.time_manager_client_expense_categories import (
    TimeManagerClientExpenseCategoryCreateBody,
    TimeManagerClientExpenseCategoryPatchBody,
)
from presentation.schemas.time_manager_client_projects import (
    TimeManagerClientProjectCreateBody,
    TimeManagerClientProjectPatchBody,
)
from presentation.schemas.time_manager_client_tasks import (
    TimeManagerClientTaskCreateBody,
    TimeManagerClientTaskPatchBody,
)

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

router = APIRouter(prefix="/api/v1/time-tracking", tags=["time_tracking"])


def _time_tracking_base_url() -> str:
    return service_base_url(get_settings().time_tracking_service_url, "Time tracking")


async def _tt_request(
    method: str,
    path: str,
    *,
    timeout: float = 10.0,
    **kwargs,
) -> httpx.Response:
    base = _time_tracking_base_url()
    if not path.startswith("/"):
        path = "/" + path
    headers = merge_upstream_headers(dict(kwargs.pop("headers", None) or {}))
    return await send_upstream_request(
        method,
        f"{base}{path}",
        timeout=timeout,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
        headers=headers,
        **kwargs,
    )


async def _tt_json(
    method: str,
    path: str,
    *,
    timeout: float = 10.0,
    **kwargs,
):
    response = await _tt_request(method, path, timeout=timeout, **kwargs)
    raise_for_upstream_status(response, "Time tracking service error")
    return response.json()


def _alias_free_payload(body: BaseModel, label: str, *, exclude_unset: bool = False) -> dict:
    try:
        return json.loads(body.model_dump_json(by_alias=False, exclude_unset=exclude_unset))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid {label} payload: {exc}") from exc


def require_view_role(user: dict = Depends(get_current_user)):
    role = (user.get("role") or "").strip()
    if role not in {
        "Главный администратор",
        "Администратор",
        "Партнер",
        "IT отдел",
        "Офис менеджер",
    }:
        raise HTTPException(
            status_code=403,
            detail="Only administrators and office managers can view time tracking users",
        )
    return user


def require_manage_role(user: dict = Depends(get_current_user)):
    role = (user.get("role") or "").strip()
    if role not in {"Главный администратор", "Администратор", "Партнер"}:
        raise HTTPException(
            status_code=403,
            detail="Only administrators can update or delete time tracking users",
        )
    return user


async def _fetch_time_tracking_user_role(auth_user_id: int) -> str | None:

    r = await _tt_request("GET", f"/users/{auth_user_id}", timeout=10.0)
    if r.status_code == 404:
        return None
    raise_for_upstream_status(r, "Time tracking service error")
    data = r.json()
    role = (data.get("role") or "").strip()
    return role or None


async def require_view_project_access(
    auth_user_id: int,
    user: dict = Depends(get_current_user),
):
    my_id = user.get("id")
    if my_id is not None and int(my_id) == auth_user_id:
        return user
    role = (user.get("role") or "").strip()
    if role in {
        "Главный администратор",
        "Администратор",
        "Партнер",
        "IT отдел",
        "Офис менеджер",
    }:
        return user
    if my_id is None:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    tt_role = await _fetch_time_tracking_user_role(int(my_id))
    if tt_role == "manager":
        scope = await _tt_managed_scope_user_ids(int(my_id))
        if auth_user_id in scope:
            return user
        raise HTTPException(
            status_code=403,
            detail="Менеджер учёта времени видит доступ к проектам только у сотрудников с общими проектами",
        )
    raise HTTPException(
        status_code=403,
        detail="Недостаточно прав для просмотра доступа к проектам",
    )


async def require_manage_project_access(
    auth_user_id: int,
    user: dict = Depends(get_current_user),
):
    role = (user.get("role") or "").strip()
    if role in {"Главный администратор", "Администратор", "Партнер"}:
        return user
    my_id = user.get("id")
    if my_id is None:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    tt_role = await _fetch_time_tracking_user_role(int(my_id))
    if tt_role == "manager":
        scope = await _tt_managed_scope_user_ids(int(my_id))
        if auth_user_id in scope:
            return user
        raise HTTPException(
            status_code=403,
            detail="Менеджер может настраивать доступ к проектам только сотрудникам с общими проектами",
        )
    raise HTTPException(
        status_code=403,
        detail="Недостаточно прав для настройки доступа к проектам",
    )


_VIEW_ROLES_TIME_ENTRIES = {
    "Главный администратор",
    "Администратор",
    "Партнер",
    "IT отдел",
    "Офис менеджер",
}

_MANAGE_ROLES_TIME_ENTRIES = {"Главный администратор", "Администратор", "Партнер"}


def _current_auth_user_id(user: dict) -> int:
    uid = user.get("id")
    if uid is None:
        raise HTTPException(status_code=403, detail="В токене нет id пользователя")
    return int(uid)


async def _tt_managed_scope_user_ids(manager_auth_user_id: int) -> set[int]:

    r = await _tt_request("GET", f"/users/managed-scope/{manager_auth_user_id}", timeout=15.0)
    if r.status_code >= 400:
        return {int(manager_auth_user_id)}
    try:
        data = r.json()
    except (TypeError, ValueError):
        return {int(manager_auth_user_id)}
    if not isinstance(data, list):
        return {int(manager_auth_user_id)}
    out: set[int] = set()
    for u in data:
        if not isinstance(u, dict):
            continue
        try:
            out.add(int(u.get("id")))
        except (TypeError, ValueError):
            continue
    if not out:
        out.add(int(manager_auth_user_id))
    return out


async def require_view_time_tracking_user_directory(user: dict = Depends(get_current_user)):

    role = (user.get("role") or "").strip()
    if role in _VIEW_ROLES_TIME_ENTRIES:
        return user
    tt_role = await _fetch_time_tracking_user_role(_current_auth_user_id(user))
    if tt_role == "manager":
        return user
    raise HTTPException(
        status_code=403,
        detail="Only administrators and office managers can view time tracking users",
    )


async def require_time_entry_read(
    auth_user_id: int,
    user: dict = Depends(get_current_user),
):

    if _current_auth_user_id(user) == auth_user_id:
        return user
    role = (user.get("role") or "").strip()
    if role in _VIEW_ROLES_TIME_ENTRIES:
        return user
    tt_role = await _fetch_time_tracking_user_role(_current_auth_user_id(user))
    if tt_role == "manager":
        scope = await _tt_managed_scope_user_ids(_current_auth_user_id(user))
        if auth_user_id in scope:
            return user
        raise HTTPException(
            status_code=403,
            detail="Менеджер учёта времени видит записи только сотрудников с общими проектами доступа",
        )
    raise HTTPException(
        status_code=403,
        detail="Можно просматривать только свои записи времени либо нужна роль офиса или менеджера учёта времени",
    )


async def require_time_entry_write(
    auth_user_id: int,
    user: dict = Depends(get_current_user),
):

    if _current_auth_user_id(user) == auth_user_id:
        return user
    role = (user.get("role") or "").strip()
    if role in _MANAGE_ROLES_TIME_ENTRIES:
        return user
    tt_role = await _fetch_time_tracking_user_role(_current_auth_user_id(user))
    if tt_role == "manager":
        scope = await _tt_managed_scope_user_ids(_current_auth_user_id(user))
        if auth_user_id in scope:
            return user
        raise HTTPException(
            status_code=403,
            detail="Менеджер учёта времени может менять записи только сотрудников с общими проектами доступа",
        )
    raise HTTPException(
        status_code=403,
        detail="Можно изменять только свои записи времени либо нужны права администратора или менеджера учёта времени",
    )


class UserUpsertBody(BaseModel):


    model_config = ConfigDict(populate_by_name=True)

    auth_user_id: int = Field(..., alias="authUserId")
    email: str
    display_name: Optional[str] = Field(None, alias="displayName")
    picture: Optional[str] = None
    position: Optional[str] = None
    role: str = ""
    is_blocked: bool = Field(False, alias="isBlocked")
    is_archived: bool = Field(False, alias="isArchived")
    weekly_capacity_hours: Optional[Decimal] = Field(None, alias="weeklyCapacityHours")


def _user_payload_bool(user: dict, snake: str, camel: str) -> bool:

    v = user.get(snake)
    if v is not None:
        return v is True or v == 1 or str(v).lower() == "true"
    v = user.get(camel)
    if v is not None:
        return v is True or v == 1 or str(v).lower() == "true"
    return False


def _self_time_tracking_user_upsert_payload(user: dict, body: UserUpsertBody) -> dict:

    my_id = _current_auth_user_id(user)
    tt_auth_role = (user.get("time_tracking_role") or user.get("timeTrackingRole") or "").strip()
    if tt_auth_role not in {"user", "manager"}:
        raise HTTPException(
            status_code=403,
            detail="Нет роли в учёте времени (сотрудник или менеджер). Обратитесь к администратору организации.",
        )
    email = (str(user.get("email") or "").strip()) or (body.email or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="У пользователя нет email для синхронизации с учётом времени")

    disp = user.get("display_name")
    if disp is None:
        disp = user.get("displayName")
    if disp is None:
        display_name = body.display_name
    else:
        s = str(disp).strip()
        display_name = s if s else None

    pic = user.get("picture")
    if pic is None:
        picture = body.picture
    else:
        s = str(pic).strip()
        picture = s if s else None

    pos = user.get("position")
    if pos is not None and str(pos).strip():
        position = str(pos).strip()
    else:
        position = None

    if tt_auth_role in {"user", "manager"} and not position:
        raise HTTPException(
            status_code=400,
            detail="Для учёта времени у сотрудника должна быть указана должность. Обратитесь к администратору.",
        )

    safe = UserUpsertBody(
        auth_user_id=my_id,
        email=email,
        display_name=display_name,
        picture=picture,
        position=position,
        role=tt_auth_role,
        is_blocked=_user_payload_bool(user, "is_blocked", "isBlocked"),
        is_archived=_user_payload_bool(user, "is_archived", "isArchived"),
        weekly_capacity_hours=body.weekly_capacity_hours,
    )
    return _alias_free_payload(safe, "user upsert")


@router.get("/users/{auth_user_id}/hourly-rates")
async def list_hourly_rates(
    auth_user_id: int,
    kind: str = Query(..., description="billable | cost"),
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_list_gateway(auth_user_id, kind, user)


@router.get("/users/{auth_user_id}/hourly-rates/{rate_id}")
async def get_hourly_rate(
    auth_user_id: int,
    rate_id: str,
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_get_gateway(auth_user_id, rate_id, user)


@router.post("/users/{auth_user_id}/hourly-rates")
async def create_hourly_rate(
    auth_user_id: int,
    body: HourlyRateCreateBody,
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_create_gateway(auth_user_id, body, user)


@router.patch("/users/{auth_user_id}/hourly-rates/{rate_id}")
async def patch_hourly_rate(
    auth_user_id: int,
    rate_id: str,
    body: HourlyRatePatchBody,
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_patch_gateway(auth_user_id, rate_id, body, user)


@router.delete("/users/{auth_user_id}/hourly-rates/{rate_id}")
async def delete_hourly_rate(
    auth_user_id: int,
    rate_id: str,
    user: dict = Depends(get_current_user),
):
    return await hourly_rates_delete_gateway(auth_user_id, rate_id, user)


@router.get("/team-workload")
async def proxy_team_workload(request: Request, _: dict = Depends(require_view_role)):
    return await _tt_json("GET", "/team-workload", timeout=20.0, params=request.query_params)


@router.get("/users/{auth_user_id}/time-entries")
async def proxy_list_time_entries(
    auth_user_id: int,
    request: Request,
    _: dict = Depends(require_time_entry_read),
):
    return await time_entries_list_gateway(auth_user_id, request)


@router.post("/users/{auth_user_id}/time-entries")
async def proxy_create_time_entry(
    auth_user_id: int,
    body: TimeEntryCreateBody,
    _: dict = Depends(require_time_entry_write),
):
    return await time_entries_create_gateway(auth_user_id, body)


@router.patch("/users/{auth_user_id}/time-entries/{entry_id}")
async def proxy_patch_time_entry(
    auth_user_id: int,
    entry_id: str,
    body: TimeEntryPatchBody,
    _: dict = Depends(require_time_entry_write),
):
    return await time_entries_patch_gateway(auth_user_id, entry_id, body)


@router.delete(
    "/users/{auth_user_id}/time-entries/{entry_id}",
    responses={
        204: {"description": "Собственная запись удалена"},
        200: {"description": "Снятие с учёта чужой записи (менеджер)"},
    },
)
async def proxy_delete_time_entry(
    auth_user_id: int,
    entry_id: str,
    body: TimeEntryDeleteBody | None = Body(None),
    _: dict = Depends(require_time_entry_write),
):
    out = await time_entries_delete_gateway(auth_user_id, entry_id, body)
    if out is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return out


@router.get("/users/{auth_user_id}/project-access")
async def proxy_get_project_access(
    auth_user_id: int,
    _: dict = Depends(require_view_project_access),
):
    return await project_access_get_gateway(auth_user_id)


@router.put("/users/{auth_user_id}/project-access")
async def proxy_put_project_access(
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


@router.get("/users/partners")
async def list_partner_users(user: dict = Depends(require_view_time_tracking_user_directory)):

    role = (user.get("role") or "").strip()
    if role in _VIEW_ROLES_TIME_ENTRIES:
        return await _tt_json("GET", "/users/partners")
    mid = _current_auth_user_id(user)
    return await _tt_json("GET", f"/users/managed-scope/{mid}/partners")


@router.get("/users")
async def list_users(user: dict = Depends(require_view_time_tracking_user_directory)):
    role = (user.get("role") or "").strip()
    if role in _VIEW_ROLES_TIME_ENTRIES:
        return await _tt_json("GET", "/users")
    mid = _current_auth_user_id(user)
    return await _tt_json("GET", f"/users/managed-scope/{mid}")


@router.post("/users")
async def upsert_user(
    body: UserUpsertBody,
    user: dict = Depends(get_current_user),
):
    org_role = (user.get("role") or "").strip()
    if org_role in {"Главный администратор", "Администратор", "Партнер"}:
        payload = _alias_free_payload(body, "user upsert")
        return await _tt_json("POST", "/users", json=payload)

    if body.auth_user_id != _current_auth_user_id(user):
        raise HTTPException(
            status_code=403,
            detail="Синхронизировать в учёте времени другого пользователя могут только главный администратор, администратор или партнёр.",
        )

    payload = _self_time_tracking_user_upsert_payload(user, body)
    return await _tt_json("POST", "/users", json=payload)


@router.get("/clients")
async def list_time_manager_clients(
    include_archived: bool = Query(False, alias="includeArchived"),
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_view_role),
):
    params: dict[str, str] = {
        "includeArchived": "true" if include_archived else "false",
    }
    if limit is not None:
        params["limit"] = str(limit)
        params["offset"] = str(offset)
    return await _tt_json("GET", "/clients", params=params)


@router.get("/clients/{client_id}/tasks")
async def list_client_tasks(client_id: str, _: dict = Depends(require_view_role)):
    return await _tt_json("GET", f"/clients/{client_id}/tasks")


@router.get("/clients/{client_id}/tasks/{task_id}")
async def get_client_task(
    client_id: str,
    task_id: str,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/clients/{client_id}/tasks/{task_id}")


@router.post("/clients/{client_id}/tasks")
async def create_client_task(
    client_id: str,
    body: TimeManagerClientTaskCreateBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "task")
    return await _tt_json("POST", f"/clients/{client_id}/tasks", json=payload)


@router.patch("/clients/{client_id}/tasks/{task_id}")
async def patch_client_task(
    client_id: str,
    task_id: str,
    body: TimeManagerClientTaskPatchBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "task", exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    return await _tt_json("PATCH", f"/clients/{client_id}/tasks/{task_id}", json=payload)


@router.delete("/clients/{client_id}/tasks/{task_id}", status_code=204)
async def delete_client_task(
    client_id: str,
    task_id: str,
    _: dict = Depends(require_manage_role),
):
    await _tt_json("DELETE", f"/clients/{client_id}/tasks/{task_id}")
    return None


@router.get("/clients/{client_id}/contacts")
async def list_client_contacts_gateway(client_id: str, _: dict = Depends(require_view_role)):
    return await _tt_json("GET", f"/clients/{client_id}/contacts")


@router.get("/clients/{client_id}/contacts/{contact_id}")
async def get_client_contact_gateway(
    client_id: str,
    contact_id: str,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/clients/{client_id}/contacts/{contact_id}")


@router.post("/clients/{client_id}/contacts")
async def create_client_contact_gateway(
    client_id: str,
    body: TimeManagerClientContactCreateBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "contact")
    return await _tt_json("POST", f"/clients/{client_id}/contacts", json=payload)


@router.patch("/clients/{client_id}/contacts/{contact_id}")
async def patch_client_contact_gateway(
    client_id: str,
    contact_id: str,
    body: TimeManagerClientContactPatchBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "contact", exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    return await _tt_json("PATCH", f"/clients/{client_id}/contacts/{contact_id}", json=payload)


@router.delete("/clients/{client_id}/contacts/{contact_id}", status_code=204)
async def delete_client_contact_gateway(
    client_id: str,
    contact_id: str,
    _: dict = Depends(require_manage_role),
):
    await _tt_json("DELETE", f"/clients/{client_id}/contacts/{contact_id}")
    return None


@router.get("/clients/{client_id}/expense-categories")
async def list_client_expense_categories(
    client_id: str,
    include_archived: bool = Query(False, alias="includeArchived"),
    _: dict = Depends(require_view_role),
):
    return await _tt_json(
        "GET",
        f"/clients/{client_id}/expense-categories",
        params={"includeArchived": "true" if include_archived else "false"},
    )


@router.get("/clients/{client_id}/expense-categories/{category_id}")
async def get_client_expense_category(
    client_id: str,
    category_id: str,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/clients/{client_id}/expense-categories/{category_id}")


@router.post("/clients/{client_id}/expense-categories")
async def create_client_expense_category(
    client_id: str,
    body: TimeManagerClientExpenseCategoryCreateBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "expense category")
    return await _tt_json("POST", f"/clients/{client_id}/expense-categories", json=payload)


@router.patch("/clients/{client_id}/expense-categories/{category_id}")
async def patch_client_expense_category(
    client_id: str,
    category_id: str,
    body: TimeManagerClientExpenseCategoryPatchBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "expense category", exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    return await _tt_json(
        "PATCH",
        f"/clients/{client_id}/expense-categories/{category_id}",
        json=payload,
    )


@router.delete("/clients/{client_id}/expense-categories/{category_id}", status_code=204)
async def delete_client_expense_category(
    client_id: str,
    category_id: str,
    _: dict = Depends(require_manage_role),
):
    await _tt_json("DELETE", f"/clients/{client_id}/expense-categories/{category_id}")
    return None


@router.get("/clients/{client_id}/projects/code-hint")
async def get_client_project_code_hint(
    client_id: str,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/clients/{client_id}/projects/code-hint")


@router.post("/clients/{client_id}/projects/{project_id}/duplicate")
async def duplicate_client_project(
    client_id: str,
    project_id: str,
    _: dict = Depends(require_manage_role),
):
    return await _tt_json("POST", f"/clients/{client_id}/projects/{project_id}/duplicate")


@router.get("/clients/{client_id}/projects/{project_id}/export")
async def export_client_project(
    client_id: str,
    project_id: str,
    export_format: Literal["json", "csv"] = Query("json", alias="format"),
    _: dict = Depends(require_view_role),
):
    r = await _tt_request(
        "GET",
        f"/clients/{client_id}/projects/{project_id}/export",
        params={"format": export_format},
    )
    raise_for_upstream_status(r, "Time tracking service error")
    out_headers: dict[str, str] = {}
    if ct := r.headers.get("content-type"):
        out_headers["Content-Type"] = ct
    if cd := r.headers.get("content-disposition"):
        out_headers["Content-Disposition"] = cd
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)


@router.get("/projects-for-expenses")
async def list_projects_for_expenses(
    include_archived: bool = Query(False, alias="includeArchived"),
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(get_current_user),
):

    params: dict[str, str] = {
        "includeArchived": "true" if include_archived else "false",
    }
    if limit is not None:
        params["limit"] = str(limit)
        params["offset"] = str(offset)
    return await _tt_json("GET", "/projects-for-expenses", params=params)


@router.get("/projects/{project_id}/expense-categories")
async def list_expense_categories_for_project(
    project_id: str,
    include_archived: bool = Query(False, alias="includeArchived"),
    _: dict = Depends(get_current_user),
):

    return await _tt_json(
        "GET",
        f"/projects/{project_id}/expense-categories",
        params={"includeArchived": "true" if include_archived else "false"},
    )


@router.get("/projects/{project_id}/time-tracking-assignees")
async def list_time_tracking_assignees_for_project(
    project_id: str,
    _: dict = Depends(get_current_user),
):

    return await _tt_json("GET", f"/projects/{project_id}/time-tracking-assignees")


@router.get("/clients/{client_id}/projects")
async def list_client_projects(
    client_id: str,
    include_archived: bool = Query(False, alias="includeArchived"),
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_view_role),
):
    params: dict[str, str] = {
        "includeArchived": "true" if include_archived else "false",
    }
    if limit is not None:
        params["limit"] = str(limit)
        params["offset"] = str(offset)
    return await _tt_json("GET", f"/clients/{client_id}/projects", params=params)


@router.get("/clients/{client_id}/projects/{project_id}")
async def get_client_project(
    client_id: str,
    project_id: str,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/clients/{client_id}/projects/{project_id}")


@router.get("/clients/{client_id}/projects/{project_id}/dashboard")
async def get_client_project_dashboard(
    client_id: str,
    project_id: str,
    date_from: Optional[str] = Query(None, alias="dateFrom"),
    date_to: Optional[str] = Query(None, alias="dateTo"),
    _: dict = Depends(require_view_role),
):
    params: dict[str, str] = {}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    dashboard = await _tt_json(
        "GET",
        f"/clients/{client_id}/projects/{project_id}/dashboard",
        timeout=30.0,
        params=params or None,
    )
    expense_params: dict[str, str] = {}
    if date_from:
        expense_params["dateFrom"] = date_from
    if date_to:
        expense_params["dateTo"] = date_to
    try:
        expenses_base = (get_settings().expenses_service_url or "").rstrip("/")
        if expenses_base:
            r = await send_upstream_request(
                "GET",
                f"{expenses_base}/expenses/project-totals/{project_id}",
                params=expense_params or None,
                timeout=10.0,
                unavailable_status=503,
                unavailable_detail="Expenses service unavailable",
            )
            if r.status_code == 200:
                exp = r.json()
                if isinstance(dashboard, dict) and isinstance(dashboard.get("totals"), dict):
                    dashboard["totals"]["expense_amount_uzs"] = exp.get("total_amount_uzs", 0)
                    dashboard["totals"]["expense_count"] = exp.get("count", 0)
    except Exception:
        pass
    return dashboard


@router.get("/clients/{client_id}/projects/{project_id}/team-workload")
async def get_project_team_workload(
    client_id: str,
    project_id: str,
    request: Request,
    _: dict = Depends(require_view_role),
):
    return await _tt_json(
        "GET",
        f"/clients/{client_id}/projects/{project_id}/team-workload",
        timeout=30.0,
        params=request.query_params,
    )


@router.post("/clients/{client_id}/projects")
async def create_client_project(
    client_id: str,
    body: TimeManagerClientProjectCreateBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "project")
    return await _tt_json("POST", f"/clients/{client_id}/projects", json=payload)


@router.patch("/clients/{client_id}/projects/{project_id}")
async def patch_client_project(
    client_id: str,
    project_id: str,
    body: TimeManagerClientProjectPatchBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "project", exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    return await _tt_json("PATCH", f"/clients/{client_id}/projects/{project_id}", json=payload)


@router.delete("/clients/{client_id}/projects/{project_id}", status_code=204)
async def delete_client_project(
    client_id: str,
    project_id: str,
    _: dict = Depends(require_manage_role),
):
    await _tt_json("DELETE", f"/clients/{client_id}/projects/{project_id}")
    return None


@router.get("/clients/{client_id}")
async def get_time_manager_client(client_id: str, _: dict = Depends(require_view_role)):
    return await _tt_json("GET", f"/clients/{client_id}")


@router.post("/clients")
async def create_time_manager_client(
    body: TimeManagerClientCreateBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "client")
    return await _tt_json("POST", "/clients", json=payload)


@router.patch("/clients/{client_id}")
async def patch_time_manager_client(
    client_id: str,
    body: TimeManagerClientPatchBody,
    _: dict = Depends(require_manage_role),
):
    payload = _alias_free_payload(body, "client", exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    return await _tt_json("PATCH", f"/clients/{client_id}", json=payload)


@router.delete("/clients/{client_id}", status_code=204)
async def delete_time_manager_client(client_id: str, _: dict = Depends(require_manage_role)):
    await _tt_json("DELETE", f"/clients/{client_id}")
    return None


@router.delete("/users/{auth_user_id}")
async def delete_user(
    auth_user_id: int,
    _: dict = Depends(require_manage_role),
):
    return await _tt_json("DELETE", f"/users/{auth_user_id}")


@router.get("/reports/meta")
async def reports_meta(_: dict = Depends(get_current_user)):
    return await _tt_json("GET", "/reports/meta")


@router.get("/reports/users-for-filter")
async def reports_users_for_filter(_: dict = Depends(require_view_role)):
    return await _tt_json("GET", "/reports/users-for-filter")


@router.get("/reports/snapshots")
async def reports_snapshots_list(_: dict = Depends(require_view_role)):
    return await _tt_json("GET", "/reports/snapshots", timeout=30.0)


@router.get("/reports/snapshots/{snapshot_id}")
async def reports_snapshot_detail(
    snapshot_id: str,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/reports/snapshots/{snapshot_id}", timeout=30.0)


@router.patch("/reports/snapshots/{snapshot_id}/rows/{row_id}")
async def reports_snapshot_row_patch(
    snapshot_id: str,
    row_id: str,
    body: dict = Body(...),
    _: dict = Depends(require_view_role),
):
    return await _tt_json(
        "PATCH",
        f"/reports/snapshots/{snapshot_id}/rows/{row_id}",
        json=body,
        timeout=30.0,
    )


@router.get("/reports/time/{group_by}")
async def reports_time(
    group_by: str,
    request: Request,
    user: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/reports/time/{group_by}", params=request.query_params, timeout=30.0)


@router.get("/reports/time/{group_by}/export")
async def reports_time_export(
    group_by: str,
    request: Request,
    user: dict = Depends(require_view_role),
):
    r = await _tt_request(
        "GET", f"/reports/time/{group_by}/export", params=request.query_params, timeout=60.0,
    )
    raise_for_upstream_status(r, "Time tracking service error")
    out_headers: dict[str, str] = {}
    if ct := r.headers.get("content-type"):
        out_headers["Content-Type"] = ct
    if cd := r.headers.get("content-disposition"):
        out_headers["Content-Disposition"] = cd
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)


@router.get("/reports/expenses/{group_by}")
async def reports_expenses(
    group_by: str,
    request: Request,
    user: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/reports/expenses/{group_by}", params=request.query_params, timeout=30.0)


@router.get("/reports/expenses/{group_by}/export")
async def reports_expenses_export(
    group_by: str,
    request: Request,
    user: dict = Depends(require_view_role),
):
    r = await _tt_request(
        "GET", f"/reports/expenses/{group_by}/export", params=request.query_params, timeout=60.0,
    )
    raise_for_upstream_status(r, "Time tracking service error")
    out_headers: dict[str, str] = {}
    if ct := r.headers.get("content-type"):
        out_headers["Content-Type"] = ct
    if cd := r.headers.get("content-disposition"):
        out_headers["Content-Disposition"] = cd
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)


@router.get("/reports/uninvoiced")
async def reports_uninvoiced(
    request: Request,
    user: dict = Depends(require_view_role),
):
    return await _tt_json("GET", "/reports/uninvoiced", params=request.query_params, timeout=30.0)


@router.get("/reports/uninvoiced/export")
async def reports_uninvoiced_export(
    request: Request,
    user: dict = Depends(require_view_role),
):
    r = await _tt_request(
        "GET", "/reports/uninvoiced/export", params=request.query_params, timeout=60.0,
    )
    raise_for_upstream_status(r, "Time tracking service error")
    out_headers: dict[str, str] = {}
    if ct := r.headers.get("content-type"):
        out_headers["Content-Type"] = ct
    if cd := r.headers.get("content-disposition"):
        out_headers["Content-Disposition"] = cd
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)


@router.get("/reports/project-budget")
async def reports_project_budget(
    request: Request,
    user: dict = Depends(require_view_role),
):
    return await _tt_json("GET", "/reports/project-budget", params=request.query_params, timeout=30.0)


@router.get("/reports/project-budget/export")
async def reports_project_budget_export(
    request: Request,
    user: dict = Depends(require_view_role),
):
    r = await _tt_request(
        "GET", "/reports/project-budget/export", params=request.query_params, timeout=60.0,
    )
    raise_for_upstream_status(r, "Time tracking service error")
    out_headers: dict[str, str] = {}
    if ct := r.headers.get("content-type"):
        out_headers["Content-Type"] = ct
    if cd := r.headers.get("content-disposition"):
        out_headers["Content-Disposition"] = cd
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)


def _invoice_actor_qs(user: dict) -> dict[str, str]:
    return {"actorAuthUserId": str(_current_auth_user_id(user))}


@router.get("/invoices/unbilled-time")
async def invoices_unbilled_time(
    request: Request,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", "/invoices/unbilled-time", params=dict(request.query_params), timeout=30.0)


@router.get("/invoices/unbilled-expenses")
async def invoices_unbilled_expenses(
    request: Request,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", "/invoices/unbilled-expenses", params=dict(request.query_params), timeout=30.0)


@router.get("/invoices/stats")
async def invoices_stats(
    request: Request,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", "/invoices/stats", params=dict(request.query_params), timeout=30.0)


@router.get("/invoices")
async def invoices_list(
    request: Request,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", "/invoices", params=dict(request.query_params), timeout=30.0)


@router.post("/invoices")
async def invoices_create(
    request: Request,
    user: dict = Depends(require_view_role),
):
    body = await request.json()
    return await _tt_json("POST", "/invoices", json=body, params=_invoice_actor_qs(user), timeout=60.0)


@router.get("/invoices/{invoice_id}/audit")
async def invoices_audit(
    invoice_id: str,
    _: dict = Depends(require_view_role),
):
    return await _tt_json("GET", f"/invoices/{invoice_id}/audit", timeout=30.0)


@router.get("/invoices/{invoice_id}")
async def invoices_get(
    invoice_id: str,
    request: Request,
    _: dict = Depends(require_view_role),
):
    return await _tt_json(
        "GET",
        f"/invoices/{invoice_id}",
        params=dict(request.query_params),
        timeout=30.0,
    )


@router.patch("/invoices/{invoice_id}")
async def invoices_patch(
    invoice_id: str,
    request: Request,
    user: dict = Depends(require_view_role),
):
    body = await request.json()
    return await _tt_json(
        "PATCH",
        f"/invoices/{invoice_id}",
        json=body,
        params=_invoice_actor_qs(user),
        timeout=60.0,
    )


@router.post("/invoices/{invoice_id}/send")
async def invoices_send(
    invoice_id: str,
    user: dict = Depends(require_view_role),
):
    return await _tt_json("POST", f"/invoices/{invoice_id}/send", params=_invoice_actor_qs(user), timeout=30.0)


@router.post("/invoices/{invoice_id}/mark-viewed")
async def invoices_mark_viewed(
    invoice_id: str,
    user: dict = Depends(require_view_role),
):
    return await _tt_json(
        "POST",
        f"/invoices/{invoice_id}/mark-viewed",
        params=_invoice_actor_qs(user),
        timeout=30.0,
    )


@router.post("/invoices/{invoice_id}/payments")
async def invoices_add_payment(
    invoice_id: str,
    request: Request,
    user: dict = Depends(require_view_role),
):
    raw = await request.body()
    if not raw.strip():
        body: dict = {}
    else:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный JSON тела запроса") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Тело запроса должно быть JSON-объектом")
        body = parsed
    return await _tt_json(
        "POST",
        f"/invoices/{invoice_id}/payments",
        json=body,
        params=_invoice_actor_qs(user),
        timeout=30.0,
    )


@router.post("/invoices/{invoice_id}/cancel")
async def invoices_cancel(
    invoice_id: str,
    user: dict = Depends(require_view_role),
):
    return await _tt_json(
        "POST",
        f"/invoices/{invoice_id}/cancel",
        params=_invoice_actor_qs(user),
        timeout=30.0,
    )


@router.delete("/invoices/{invoice_id}", status_code=204)
async def invoices_delete_draft(
    invoice_id: str,
    user: dict = Depends(require_view_role),
):
    r = await _tt_request(
        "DELETE",
        f"/invoices/{invoice_id}",
        params=_invoice_actor_qs(user),
        timeout=30.0,
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return None
