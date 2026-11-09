from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException, Request

from application.access import is_manager, is_super_admin
from infrastructure.config import get_settings
from presentation.outlook_scope import outlook_path_allowed


@dataclass(frozen=True)
class Viewer:
    user_id: int
    name: str
    manager: bool
    super_admin: bool
    authorization: str
    token_typ: str | None = None
    token_scope: str | None = None


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
    x_outlook_device_id: Annotated[str | None, Header(alias="X-Outlook-Device-Id")] = None,
) -> Viewer:
    settings = get_settings()
    auth = await _resolve_auth_header(request, authorization)
    headers = {"Authorization": auth}
    if x_outlook_device_id:
        headers["X-Outlook-Device-Id"] = x_outlook_device_id
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.auth_service_url.rstrip('/')}/users/me",
                headers=headers,
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
    token_typ = data.get("token_typ")
    token_scope = data.get("token_scope")
    if token_typ == "outlook_fm":
        if not outlook_path_allowed(request.method, request.url.path):
            raise HTTPException(
                status_code=403,
                detail="Outlook token cannot access this File Manager endpoint",
            )
    role = data.get("role")
    position = data.get("position")
    name = str(data.get("display_name") or data.get("email") or f"#{user_id}").strip()
    return Viewer(
        user_id=user_id,
        name=name[:200],
        manager=is_manager(role, position),
        super_admin=is_super_admin(role),
        authorization=auth,
        token_typ=str(token_typ) if token_typ else None,
        token_scope=str(token_scope) if token_scope else None,
    )
