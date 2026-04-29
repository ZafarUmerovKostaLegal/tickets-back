

from __future__ import annotations

from typing import Optional

from fastapi import Request

from infrastructure.config import get_settings


def access_token_from_request(request: Request, authorization: Optional[str]) -> str:
    raw = (authorization or "").strip()
    if raw:
        return raw.replace("Bearer ", "", 1).strip()
    name = (get_settings().auth_session_cookie_name or "").strip()
    if not name:
        return ""
    v = request.cookies.get(name)
    return (v or "").strip()
