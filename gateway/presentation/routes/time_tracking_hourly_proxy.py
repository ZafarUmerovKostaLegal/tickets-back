

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers
from infrastructure.upstream_http import (
    raise_for_upstream_status,
    send_upstream_request,
    service_base_url,
)

ROLES_CAN_VIEW = {"Главный администратор", "Администратор", "Партнер", "IT отдел", "Офис менеджер"}
ROLES_CAN_MANAGE = {"Главный администратор", "Администратор", "Партнер"}
ROLES_ADMIN_ONLY = {"Главный администратор", "Администратор"}


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return await verify_bearer_and_get_user(request, authorization)


def _role(user: dict) -> str:
    return (user.get("role") or "").strip()


def _ensure_billable_rates_view(user: dict) -> None:
    if _role(user) not in ROLES_CAN_VIEW:
        raise HTTPException(
            status_code=403,
            detail="Оплачиваемые ставки доступны администраторам и менеджерам",
        )


def _ensure_cost_rates_view(user: dict) -> None:
    if _role(user) not in ROLES_ADMIN_ONLY:
        raise HTTPException(
            status_code=403,
            detail="Ставки себестоимости доступны только администраторам",
        )


def _ensure_manage_billable_rates(user: dict) -> None:
    if _role(user) not in ROLES_CAN_MANAGE:
        raise HTTPException(
            status_code=403,
            detail="Недостаточно прав для изменения оплачиваемых ставок",
        )


def _ensure_manage_cost_rates(user: dict) -> None:
    if _role(user) not in ROLES_ADMIN_ONLY:
        raise HTTPException(
            status_code=403,
            detail="Ставки себестоимости может менять только администратор",
        )


def _time_tracking_base() -> str:
    return service_base_url(get_settings().time_tracking_service_url, "Time tracking")


async def _tt_get_hourly_rate(base: str, auth_user_id: int, rate_id: str) -> dict[str, Any] | None:
    r = await send_upstream_request(
        "GET",
        f"{base}/users/{auth_user_id}/hourly-rates/{rate_id}",
        headers=merge_upstream_headers(),
        timeout=10.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    if r.status_code == 404:
        return None
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()


class HourlyRateCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rate_kind: str = Field(..., alias="rateKind")
    amount: Decimal
    currency: str = "USD"
    valid_from: Optional[date] = Field(None, alias="validFrom")
    valid_to: Optional[date] = Field(None, alias="validTo")


class HourlyRatePatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    valid_from: Optional[date] = Field(None, alias="validFrom")
    valid_to: Optional[date] = Field(None, alias="validTo")


async def hourly_rates_list_gateway(auth_user_id: int, kind: str, user: dict) -> Any:
    if kind not in ("billable", "cost"):
        raise HTTPException(status_code=400, detail="kind must be billable or cost")
    if kind == "cost":
        _ensure_cost_rates_view(user)
    else:
        _ensure_billable_rates_view(user)
    base = _time_tracking_base()
    r = await send_upstream_request(
        "GET",
        f"{base}/users/{auth_user_id}/hourly-rates",
        params={"kind": kind},
        headers=merge_upstream_headers(),
        timeout=10.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()


async def hourly_rates_get_gateway(auth_user_id: int, rate_id: str, user: dict) -> Any:
    base = _time_tracking_base()
    data = await _tt_get_hourly_rate(base, auth_user_id, rate_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Ставка не найдена")
    if data.get("rate_kind") == "cost":
        _ensure_cost_rates_view(user)
    else:
        _ensure_billable_rates_view(user)
    return data


async def hourly_rates_create_gateway(auth_user_id: int, body: HourlyRateCreateBody, user: dict) -> Any:
    rk = (body.rate_kind or "").strip()
    if rk == "cost":
        _ensure_manage_cost_rates(user)
    elif rk == "billable":
        _ensure_manage_billable_rates(user)
    else:
        raise HTTPException(status_code=400, detail="rateKind must be billable or cost")
    base = _time_tracking_base()
    r = await send_upstream_request(
        "POST",
        f"{base}/users/{auth_user_id}/hourly-rates",
        json=body.model_dump(mode="json", by_alias=False),
        headers=merge_upstream_headers(),
        timeout=10.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()


async def hourly_rates_patch_gateway(
    auth_user_id: int,
    rate_id: str,
    body: HourlyRatePatchBody,
    user: dict,
) -> Any:
    base = _time_tracking_base()
    existing = await _tt_get_hourly_rate(base, auth_user_id, rate_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Ставка не найдена")
    if existing.get("rate_kind") == "cost":
        _ensure_manage_cost_rates(user)
    else:
        _ensure_manage_billable_rates(user)
    payload = body.model_dump(exclude_unset=True, mode="json")
    if not payload:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    r = await send_upstream_request(
        "PATCH",
        f"{base}/users/{auth_user_id}/hourly-rates/{rate_id}",
        json=payload,
        headers=merge_upstream_headers(),
        timeout=10.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()


async def hourly_rates_delete_gateway(auth_user_id: int, rate_id: str, user: dict) -> Any:
    base = _time_tracking_base()
    existing = await _tt_get_hourly_rate(base, auth_user_id, rate_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Ставка не найдена")
    if existing.get("rate_kind") == "cost":
        _ensure_manage_cost_rates(user)
    else:
        _ensure_manage_billable_rates(user)
    r = await send_upstream_request(
        "DELETE",
        f"{base}/users/{auth_user_id}/hourly-rates/{rate_id}",
        headers=merge_upstream_headers(),
        timeout=10.0,
        unavailable_status=503,
        unavailable_detail="Time tracking service unavailable",
    )
    raise_for_upstream_status(r, "Time tracking service error")
    return r.json()
