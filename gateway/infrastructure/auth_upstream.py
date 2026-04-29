

from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import Header, HTTPException, Request

from infrastructure.config import get_settings
from infrastructure.upstream_http import (
    raise_for_upstream_status,
    send_upstream_request,
    service_base_url,
)


def auth_service_base() -> str:
    return service_base_url(get_settings().auth_service_url, "Auth")


def access_token_from_request(request: Request, authorization: Optional[str]) -> str:
    raw = (authorization or "").strip()
    if raw:
        return raw.replace("Bearer ", "", 1).strip()
    name = (get_settings().auth_session_cookie_name or "").strip()
    if not name:
        return ""
    return (request.cookies.get(name) or "").strip()


async def _fetch_user_me_with_bearer_token(token: str) -> dict:
    if not (token or "").strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    r = await send_upstream_request(
        "GET",
        f"{auth_service_base()}/users/me",
        headers={"Authorization": f"Bearer {token.strip()}"},
        timeout=10.0,
        unavailable_status=503,
        unavailable_detail="Auth service unavailable",
    )
    raise_for_upstream_status(
        r,
        "Auth service error",
        status_detail_map={
            401: "Invalid or expired token",
        },
    )
    return r.json()


async def verify_bearer_and_get_user(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict:

    token = access_token_from_request(request, authorization)
    return await _fetch_user_me_with_bearer_token(token)


async def verify_access_token_plain(token: str) -> dict:

    return await _fetch_user_me_with_bearer_token(token)


async def auth_service_request(
    method: str,
    path: str,
    authorization: Optional[str],
    *,
    timeout: float = 30.0,
    **kwargs: Any,
) -> httpx.Response:

    base = auth_service_base()
    if not path.startswith("/"):
        path = "/" + path
    url = f"{base}{path}"
    headers = dict(kwargs.pop("headers", None) or {})
    if authorization:
        headers["Authorization"] = authorization
    return await send_upstream_request(
        method,
        url,
        headers=headers,
        timeout=timeout,
        unavailable_status=503,
        unavailable_detail="Auth service unavailable",
        **kwargs,
    )
