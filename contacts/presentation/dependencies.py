from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException

from infrastructure.upstream import auth_get

HIDDEN_EMAILS = frozenset({"admin@local"})
HIDDEN_LOCAL_PARTS = frozenset({"admin", "info"})
HIDDEN_DISPLAY_NAMES = frozenset({"главный администратор"})

MANAGE_ORG_ROLES = frozenset({"главный администратор", "администратор", "партнер", "партнёр"})


def _norm_role(role: str | None) -> str:
    return (role or "").strip().lower().replace("ё", "е")


def is_hidden_system_user(user: dict[str, Any]) -> bool:
    email = (user.get("email") or "").strip().lower()
    if email in HIDDEN_EMAILS:
        return True
    if email and "@" in email:
        local = email.split("@", 1)[0]
        if local in HIDDEN_LOCAL_PARTS:
            return True
    display = (user.get("display_name") or user.get("displayName") or "").strip().lower().replace("ё", "е")
    return display in HIDDEN_DISPLAY_NAMES


async def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    data = await auth_get("/users/me", authorization=authorization.strip())
    if not isinstance(data, dict) or data.get("id") is None:
        raise HTTPException(status_code=401, detail="Invalid user payload")
    return data


def require_colleagues_access(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user.get("is_archived"):
        raise HTTPException(status_code=403, detail="Archived users cannot access contacts")
    return user


def _tt_role(user: dict[str, Any]) -> str:
    return (user.get("time_tracking_role") or user.get("timeTrackingRole") or "").strip()


def require_client_contacts_view(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    tt = _tt_role(user)
    if tt in {"user", "manager"}:
        return user
    if _norm_role(user.get("role")) in MANAGE_ORG_ROLES:
        return user
    if _norm_role(user.get("role")) in {"it отдел", "офис менеджер"}:
        return user
    raise HTTPException(
        status_code=403,
        detail="Доступ к контактам клиентов только для ролей учёта времени user/manager или администраторов",
    )


def require_client_contacts_manage(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if _norm_role(user.get("role")) in MANAGE_ORG_ROLES:
        return user
    raise HTTPException(status_code=403, detail="Only administrators and partners can manage client contacts")
