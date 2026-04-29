import time
from collections import defaultdict

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel

from infrastructure.config import get_settings
from infrastructure.upstream_http import (
    raise_for_upstream_status,
    send_upstream_request,
    service_base_url,
    upstream_error_detail,
)

router = APIRouter(prefix="/api/v1/auth/admin", tags=["auth"])
def _auth_base() -> str:
    return service_base_url(get_settings().auth_service_url, "Auth")


_ADMIN_LOGIN_LIMIT = 5
_ADMIN_LOGIN_WINDOW = 900
_admin_login_attempts: dict[str, list[float]] = defaultdict(list)

_BOOTSTRAP_LIMIT = 3
_BOOTSTRAP_WINDOW = 86400
_bootstrap_attempts: dict[str, list[float]] = defaultdict(list)


def _check_bootstrap_rate_limit(client_ip: str) -> None:
    now = time.time()
    attempts = _bootstrap_attempts[client_ip]
    attempts[:] = [t for t in attempts if now - t < _BOOTSTRAP_WINDOW]
    if len(attempts) >= _BOOTSTRAP_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many bootstrap attempts. Try again in 24 hours.",
        )
    attempts.append(now)


def _check_admin_rate_limit(client_ip: str) -> None:
    now = time.time()
    attempts = _admin_login_attempts[client_ip]
    attempts[:] = [t for t in attempts if now - t < _ADMIN_LOGIN_WINDOW]
    if len(attempts) >= _ADMIN_LOGIN_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again in 15 minutes.",
        )
    attempts.append(now)


class AdminLoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def admin_login(body: AdminLoginBody, request: Request):

    client_ip = request.client.host if request.client else "unknown"
    _check_admin_rate_limit(client_ip)
    r = await send_upstream_request(
        "POST",
        f"{_auth_base()}/auth/admin-login",
        json={"username": body.username, "password": body.password},
        timeout=10.0,
        unavailable_status=502,
        unavailable_detail="Auth service unavailable",
    )
    raise_for_upstream_status(
        r,
        "Auth error",
        status_detail_map={
            401: "Invalid username or password",
            404: "Auth service does not support admin login. Rebuild and restart the auth service (docker-compose up -d --build auth).",
        },
    )
    return r.json()


@router.get("/bootstrap/status")
async def admin_bootstrap_status():

    r = await send_upstream_request(
        "GET",
        f"{_auth_base()}/auth/admin-bootstrap/status",
        timeout=10.0,
        unavailable_status=502,
        unavailable_detail="Auth service unavailable",
    )
    raise_for_upstream_status(r, "Auth error")
    return r.json()


@router.post("/bootstrap")
async def admin_bootstrap(request: Request, body: dict = Body(...)):

    client_ip = request.client.host if request.client else "unknown"
    _check_bootstrap_rate_limit(client_ip)
    r = await send_upstream_request(
        "POST",
        f"{_auth_base()}/auth/admin-bootstrap",
        json=body,
        timeout=15.0,
        unavailable_status=502,
        unavailable_detail="Auth service unavailable",
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=upstream_error_detail(r, "Auth error"))
    return r.json()
