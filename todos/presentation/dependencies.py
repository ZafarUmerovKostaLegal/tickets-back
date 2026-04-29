

from typing import Annotated

import httpx
from fastapi import Header, HTTPException, Request

from infrastructure.config import get_settings


async def get_current_user_id(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int:

    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{settings.auth_service_url.rstrip('/')}/users/me",
                headers={"Authorization": authorization},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Auth service unavailable")
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    data = r.json()
    user_id = data.get("id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid user response")
    try:
        return int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user id in auth response") from None
