from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException, Request

from infrastructure.config import get_settings


@dataclass(frozen=True)
class CurrentEmployee:
    id: int
    display_name: str
    role: str
    is_archived: bool


async def _fetch_auth_user(authorization: str) -> dict:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{settings.auth_service_url.rstrip('/')}/users/me",
                headers={"Authorization": authorization},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from None
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


async def get_current_employee(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> CurrentEmployee:
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    data = await _fetch_auth_user(authorization.strip())
    user_id = data.get("id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid user response")
    if data.get("is_archived"):
        raise HTTPException(status_code=403, detail="Archived employees cannot use chat")
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user id") from None
    return CurrentEmployee(
        id=uid,
        display_name=(data.get("display_name") or data.get("email") or "").strip(),
        role=(data.get("role") or "Сотрудник").strip(),
        is_archived=bool(data.get("is_archived")),
    )


async def get_current_user_id(
    employee: Annotated[CurrentEmployee, Depends(get_current_employee)],
) -> int:
    return employee.id
