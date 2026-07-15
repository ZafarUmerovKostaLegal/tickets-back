

from __future__ import annotations

from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException, Query

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
        detail = f"Auth service error (HTTP {r.status_code})"
        if r.status_code == 429:
            detail = "Auth service rate limited (HTTP 429)"
        raise HTTPException(status_code=503, detail=detail)
    data = r.json()
    if not isinstance(data, dict) or data.get("id") is None:
        raise HTTPException(status_code=401, detail="Invalid user payload")
    return data


async def require_tt_reports_viewer(
    user: dict = Depends(require_bearer_user),
) -> dict:
    from application.access_control import ensure_can_view_tt_reports

    ensure_can_view_tt_reports(user)
    return user


async def invoice_actor_auth_user_id(
    user: dict = Depends(require_bearer_user),
    actor_auth_user_id: int | None = Query(None, alias="actorAuthUserId"),
) -> int:
    """Исполнитель действия со счётом: явный query-параметр или id из JWT (без query не будет 422)."""
    if actor_auth_user_id is not None:
        if actor_auth_user_id < 0:
            raise HTTPException(status_code=400, detail="actorAuthUserId")
        return actor_auth_user_id
    uid = user.get("id")
    if uid is None:
        raise HTTPException(status_code=400, detail="Не удалось определить пользователя (actorAuthUserId)")
    try:
        return int(uid)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректный id пользователя") from exc
