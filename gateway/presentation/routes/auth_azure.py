import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from infrastructure.config import get_settings
from infrastructure.oauth_state_jwt import parse_oauth_state_token

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth/azure", tags=["auth"])


def _clear_oauth_cookies(resp: RedirectResponse) -> None:
    resp.delete_cookie("oauth_state_nonce", path="/")
    resp.delete_cookie("oauth_target", path="/")


@router.get(
    "/login",
    summary="Azure Login",
    description="Редирект на вход через Microsoft. target=admin — после входа редирект на админ-панель (state=admin устарел).",
)
async def azure_login(
    target: str = Query("main"),
    state: Optional[str] = Query(None, description="Устарело: используйте target=admin"),
):
    settings = get_settings()
    t = "admin" if (state == "admin" or target == "admin") else "main"
    url = f"{settings.auth_service_url}/auth/login?target={t}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, follow_redirects=False)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail="Auth service unavailable. Check: docker compose ps auth; docker compose logs auth",
        ) from e
    if r.status_code in (301, 302, 303, 307, 308):
        resp = RedirectResponse(url=r.headers["location"], status_code=r.status_code)
        try:
            cookies = r.headers.get_list("set-cookie")
        except AttributeError:
            cookies = []
        for c in cookies:
            resp.headers.append("set-cookie", c)
        return resp
    raise HTTPException(
        status_code=502,
        detail="Auth service unavailable. Check: docker compose ps auth; docker compose logs auth",
    )


@router.post("/session/logout", status_code=204, summary="Сброс серверной сессии (инвалидация JWT)")
async def session_logout(request: Request):

    settings = get_settings()
    url = f"{settings.auth_service_url.rstrip('/')}/auth/logout"
    headers: dict[str, str] = {}
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    cookie_header = request.headers.get("Cookie")
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail="Auth service unavailable",
        ) from e
    out = Response(status_code=r.status_code)
    for key, value in r.headers.raw:
        if key.lower() == b"set-cookie":
            out.headers.append("set-cookie", value.decode("latin-1"))
    if r.status_code == 401:
        return out
    if r.status_code != 204:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Logout failed")
    return out


@router.get(
    "/logout",
    summary="Logout",
    description="Редирект на выход из Microsoft.",
)
async def azure_logout():
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{settings.auth_service_url}/auth/logout",
                follow_redirects=False,
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail="Auth service unavailable",
        ) from e
    if r.status_code in (301, 302, 303, 307, 308):
        return RedirectResponse(url=r.headers["location"], status_code=r.status_code)
    raise HTTPException(status_code=502, detail="Auth service unavailable")


def _redirect_auth_failed(settings, target_t: str) -> RedirectResponse:
    base = settings.admin_frontend_url if target_t == "admin" else settings.frontend_url
    base = (base or settings.frontend_url or "http://localhost").rstrip("/")
    path = "/index.html?error=auth_failed" if target_t == "admin" else "/login?error=auth_failed"
    resp = RedirectResponse(url=base + path, status_code=302)
    _clear_oauth_cookies(resp)
    return resp


@router.get("/callback")
async def azure_callback(
    request: Request,
    code: str,
    state: Optional[str] = Query(None),
):
    settings = get_settings()
    try:
        target_t = parse_oauth_state_token(
            state,
            jwt_secret=settings.jwt_secret,
            jwt_algorithm=settings.jwt_algorithm or "HS256",
        )
        if target_t is None:
            nonce_ok = (request.cookies.get("oauth_state_nonce") or "").strip()
            cookie_tgt = (request.cookies.get("oauth_target") or "main").strip()
            if state and nonce_ok and state == nonce_ok:
                target_t = "admin" if cookie_tgt == "admin" else "main"
        if target_t is None:
            base = (settings.frontend_url or "http://localhost").rstrip("/")
            resp = RedirectResponse(url=base + "/login?error=oauth_state", status_code=302)
            _clear_oauth_cookies(resp)
            return resp

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{settings.auth_service_url.rstrip('/')}/auth/exchange",
                json={"code": code},
            )
        if r.status_code != 200:
            _log.warning(
                "auth exchange failed: status=%s body=%s",
                r.status_code,
                (r.text or "")[:500],
            )
            return _redirect_auth_failed(settings, target_t)
        try:
            data = r.json()
        except Exception as e:
            _log.warning("auth exchange returned non-JSON: %s", e)
            return _redirect_auth_failed(settings, target_t)
        access_token = data.get("access_token", "")

        if target_t == "admin" and settings.admin_frontend_url:
            base = settings.admin_frontend_url.rstrip("/")
            callback_path = "/auth/callback.html"
        else:
            base = settings.frontend_url.rstrip("/")
            callback_path = "/auth/callback"
        s = settings
        if s.auth_set_session_cookie and access_token:
            redirect_url = f"{base}{callback_path}"
        else:
            redirect_url = f"{base}{callback_path}#access_token={access_token}"
        resp = RedirectResponse(
            url=redirect_url,
            status_code=302,
        )
        if s.auth_set_session_cookie and access_token:
            ss = (s.auth_session_cookie_samesite or "lax").strip().lower()
            if ss not in ("lax", "strict", "none"):
                ss = "lax"
            resp.set_cookie(
                key=s.auth_session_cookie_name,
                value=access_token,
                max_age=int(s.jwt_expire_minutes * 60),
                httponly=True,
                secure=s.auth_session_cookie_secure,
                samesite=ss,
                path="/",
            )
        _clear_oauth_cookies(resp)
        return resp
    except httpx.RequestError as e:
        _log.exception("auth exchange unreachable: %s", e)
        base = (settings.frontend_url or "http://localhost").rstrip("/")
        resp = RedirectResponse(url=base + "/login?error=auth_upstream", status_code=302)
        _clear_oauth_cookies(resp)
        return resp
    except Exception as e:
        _log.exception("azure callback failed: %s", e)
        base = (settings.frontend_url or "http://localhost").rstrip("/")
        resp = RedirectResponse(url=base + "/login?error=callback_failed", status_code=302)
        _clear_oauth_cookies(resp)
        return resp
