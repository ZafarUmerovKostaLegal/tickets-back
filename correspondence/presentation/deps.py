from typing import Optional

import httpx
from fastapi import Header, HTTPException

from infrastructure.config import get_settings

ROLES_VIEW = {
    "Главный администратор",
    "Администратор",
    "Партнер",
    "IT отдел",
    "Офис менеджер",
    "Офис-менеджер",
    "Сотрудник",
}
ROLES_MANAGE = {
    "Главный администратор",
    "Администратор",
    "Партнер",
    "Офис менеджер",
    "Офис-менеджер",
}


def _normalize_role_key(role: str) -> str:
    return (role or "").strip().lower().replace("ё", "е")


def _role_in_set(role: str, allowed: set[str]) -> bool:
    rk = _normalize_role_key(role)
    if not rk:
        return False
    for a in allowed:
        if _normalize_role_key(a) == rk:
            return True
    return False


async def get_current_user(authorization: Optional[str] = Header(None, alias="Authorization")):
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    settings = get_settings()
    base = settings.auth_service_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{base}/users/me",
                headers={"Authorization": authorization},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Auth service unavailable")
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


def check_view_role(user: dict) -> None:
    if not _role_in_set(user.get("role") or "", ROLES_VIEW):
        raise HTTPException(status_code=403, detail="Недостаточно прав для раздела корреспонденции")


def check_manage_role(user: dict) -> None:
    if not _role_in_set(user.get("role") or "", ROLES_MANAGE):
        raise HTTPException(
            status_code=403,
            detail="Действие доступно администратору, партнёру или делопроизводителю",
        )
