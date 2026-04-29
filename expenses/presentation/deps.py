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
    "Сотрудник",
}
ROLES_MODERATE = {"Главный администратор", "Администратор", "Партнер"}
ROLES_ADMIN_EDIT = {"Главный администратор", "Администратор"}

MAIN_ADMIN_ROLE = "Главный администратор"


def _normalize_role_key(role: str) -> str:

    r = (role or "").strip().lower().replace("ё", "е")
    return r


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
        raise HTTPException(
            status_code=403,
            detail="Недостаточно прав для раздела расходов",
        )


def check_moderate_role(user: dict) -> None:
    if not _role_in_set(user.get("role") or "", ROLES_MODERATE):
        raise HTTPException(
            status_code=403,
            detail="Действие доступно только ролям модерации (администратор, партнёр)",
        )


def is_admin_editor(user: dict) -> bool:
    return _role_in_set(user.get("role") or "", ROLES_ADMIN_EDIT)


def check_main_admin(user: dict) -> None:

    if (user.get("role") or "").strip() != MAIN_ADMIN_ROLE:
        raise HTTPException(
            status_code=403,
            detail="Действие доступно только главному администратору",
        )


def is_moderator(user: dict) -> bool:
    return _role_in_set(user.get("role") or "", ROLES_MODERATE)


def is_time_tracking_manager(user: dict) -> bool:

    tt = (user.get("time_tracking_role") or user.get("timeTrackingRole") or "").strip().lower()
    return tt == "manager"


def created_by_filter_for_user(user: dict) -> int | None:

    if _normalize_role_key(user.get("role") or "") == _normalize_role_key("Сотрудник"):
        return int(user["id"])
    return None


def ensure_not_moderating_own_expense(user: dict, created_by_user_id: int) -> None:

    if get_settings().expense_allow_self_moderation:
        return
    if is_moderator(user) and int(user["id"]) == int(created_by_user_id):
        raise HTTPException(
            status_code=403,
            detail="Нельзя модерировать собственную заявку",
        )
