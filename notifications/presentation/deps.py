

from __future__ import annotations

from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException

from infrastructure.config import get_settings

_ROLES_CAN_WRITE = frozenset({"Партнер", "IT отдел", "Офис менеджер"})


async def require_bearer_user(
    authorization: str | None = Header(None, alias="Authorization"),
) -> dict[str, Any]:
    settings = get_settings()
    base = (settings.auth_service_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail="Сервис уведомлений: не задан AUTH_SERVICE_URL",
        )
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{base}/users/me",
                headers={"Authorization": authorization.strip()},
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from e
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    data = r.json()
    if not isinstance(data, dict) or data.get("id") is None:
        raise HTTPException(status_code=401, detail="Invalid user payload")
    return data


async def require_partner_it_office_write(user: dict = Depends(require_bearer_user)) -> dict:
    role = (user.get("role") or "").strip()
    if role not in _ROLES_CAN_WRITE:
        raise HTTPException(
            status_code=403,
            detail="Создание и изменение уведомлений доступно только партнёру, IT и офис-менеджеру",
        )
    return user
