

from __future__ import annotations

from typing import Any

import httpx
from fastapi import Header, HTTPException

from infrastructure.config import get_settings


async def require_bearer_user(
    authorization: str | None = Header(None, alias="Authorization"),
) -> dict[str, Any]:

    settings = get_settings()
    base = (settings.auth_service_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail="Сервис учёта времени: не задан AUTH_SERVICE_URL, проверка токена невозможна",
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
