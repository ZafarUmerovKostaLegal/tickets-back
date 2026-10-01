from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException, Request

from infrastructure.access import can_access_hr
from infrastructure.config import get_settings


async def _resolve_auth_header(request: Request, authorization: str | None) -> str:
    settings = get_settings()
    auth = (authorization or "").strip()
    if not auth and settings.auth_session_cookie_name:
        raw = (request.cookies.get(settings.auth_session_cookie_name) or "").strip()
        if raw:
            auth = f"Bearer {raw}"
    if not auth:
        raise HTTPException(status_code=401, detail="Authorization required")
    return auth


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> dict:
    settings = get_settings()
    auth = await _resolve_auth_header(request, authorization)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.auth_service_url.rstrip('/')}/users/me",
                headers={"Authorization": auth},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from exc
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if response.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    data = response.json()
    if not isinstance(data, dict) or data.get("id") is None:
        raise HTTPException(status_code=401, detail="Invalid user response")
    try:
        data = {**data, "id": int(data["id"])}
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid user id in auth response") from exc
    return data


async def require_hr_user(user: dict = Depends(get_current_user)) -> dict:
    if not can_access_hr(user.get("role"), user.get("position")):
        raise HTTPException(status_code=403, detail="HR доступен администраторам и партнёрам")
    return user
