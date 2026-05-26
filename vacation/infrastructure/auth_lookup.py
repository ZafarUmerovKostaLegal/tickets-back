from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import HTTPException

from infrastructure.config import get_settings

_log = logging.getLogger("vacation.auth_lookup")


@dataclass(frozen=True)
class AuthUser:
    id: int
    email: str
    display_name: str | None
    picture: str | None
    role: str
    position: str | None
    is_archived: bool


def _to_user(data: dict) -> AuthUser:
    return AuthUser(
        id=int(data["id"]),
        email=str(data.get("email") or ""),
        display_name=(data.get("display_name") or None),
        picture=(data.get("picture") or None),
        role=str(data.get("role") or "").strip(),
        position=(data.get("position") or None),
        is_archived=bool(data.get("is_archived")),
    )


async def get_me(authorization: str) -> AuthUser:
    settings = get_settings()
    base = (settings.auth_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="AUTH_SERVICE_URL not configured for vacation")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/users/me", headers={"Authorization": authorization})
    except httpx.RequestError as exc:
        _log.warning("auth /users/me unreachable: %r", exc)
        raise HTTPException(status_code=503, detail="Auth service unavailable") from None
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    data = r.json()
    if data.get("is_archived"):
        raise HTTPException(status_code=403, detail="Archived users cannot use vacation features")
    return _to_user(data)


async def get_user_public(user_id: int, authorization: str) -> Optional[AuthUser]:
    settings = get_settings()
    base = (settings.auth_service_url or "").rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{base}/users/{int(user_id)}/public",
                headers={"Authorization": authorization},
            )
    except httpx.RequestError:
        return None
    if r.status_code != 200:
        return None
    return _to_user(r.json())


async def list_partners(authorization: str) -> list[AuthUser]:
    """Список партнёров для выбора в заявке (любой авторизованный)."""

    settings = get_settings()
    base = (settings.auth_service_url or "").rstrip("/")
    if not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{base}/users/partners",
                headers={"Authorization": authorization},
            )
    except httpx.RequestError:
        return []
    if r.status_code != 200:
        return []
    data = r.json() or {}
    items = data.get("items") or []
    return [
        AuthUser(
            id=int(it["id"]),
            email=str(it.get("email") or ""),
            display_name=(it.get("display_name") or None),
            picture=(it.get("picture") or None),
            role="Партнер",
            position=(it.get("position") or None),
            is_archived=bool(it.get("is_archived")),
        )
        for it in items
    ]
