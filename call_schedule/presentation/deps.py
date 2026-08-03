
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Header, HTTPException, Request

from infrastructure.config import get_settings

_ADMIN_ROLES = frozenset({"Главный администратор", "Администратор"})


def _normalize_role(role: str | None) -> str:
    return (role or "").strip().lower().replace("ё", "е")


def is_admin_user(user: dict) -> bool:
    rk = _normalize_role(user.get("role") if isinstance(user, dict) else None)
    for allowed in _ADMIN_ROLES:
        if _normalize_role(allowed) == rk:
            return True
    return False


async def _resolve_auth_header(
    request: Request,
    authorization: str | None,
) -> str:
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
            r = await client.get(
                f"{settings.auth_service_url.rstrip('/')}/users/me",
                headers={"Authorization": auth},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Auth service unavailable")
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    data = r.json()
    if not isinstance(data, dict) or data.get("id") is None:
        raise HTTPException(status_code=401, detail="Invalid user response")
    try:
        data = {**data, "id": int(data["id"])}
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=401, detail="Invalid user id in auth response") from e
    return data


async def get_current_user_id(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int:
    user = await get_current_user(request, authorization)
    return int(user["id"])
