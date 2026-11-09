from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException, Request

from application.access import is_manager, is_super_admin
from infrastructure.config import get_settings


@dataclass(frozen=True)
class Viewer:
    user_id: int
    name: str
    manager: bool
    super_admin: bool
    authorization: str


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


async def get_viewer(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Viewer:
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
        user_id = int(data["id"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid user id in auth response") from exc
    role = data.get("role")
    position = data.get("position")
    name = str(data.get("display_name") or data.get("email") or f"#{user_id}").strip()
    return Viewer(
        user_id=user_id,
        name=name[:200],
        manager=is_manager(role, position),
        super_admin=is_super_admin(role),
        authorization=auth,
    )
