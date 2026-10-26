from __future__ import annotations

from typing import Any

import httpx
from fastapi import Header, HTTPException

from application.security import agent_token_configured, agent_token_matches
from infrastructure.config import get_settings


def require_agent_token(authorization: str | None = Header(default=None, alias="Authorization")) -> None:
    settings = get_settings()
    if not agent_token_configured(settings.agent_token):
        raise HTTPException(status_code=503, detail="Токен агента не настроен")
    if not agent_token_matches(authorization or "", settings.agent_token):
        raise HTTPException(status_code=401, detail="Неверный токен агента")


async def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    settings = get_settings()
    base = settings.auth_service_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{base}/users/me",
                headers={"Authorization": authorization.strip()},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from exc
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if response.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    data = response.json()
    if not isinstance(data, dict) or data.get("id") is None:
        raise HTTPException(status_code=401, detail="Invalid user payload")
    return data


def journal_login_allowed(user: dict[str, Any]) -> bool:
    settings = get_settings()
    allowed = {
        part.strip().lower()
        for part in (settings.journal_logins or "").split(",")
        if part.strip()
    }
    email = str(user.get("email") or "").strip().lower()
    local = email.split("@", 1)[0] if "@" in email else email
    return local in allowed
