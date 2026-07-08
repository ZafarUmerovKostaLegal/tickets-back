import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from infrastructure.config import get_settings
from infrastructure.oauth_state_jwt import parse_oauth_state_token

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth/azure", tags=["auth"])


class AzureExchangeBody(BaseModel):
    code: str


@router.post(
    "/exchange",
    summary="Обмен Azure authorization code на JWT (мобильные и native-клиенты)",
    description=(
        "После OAuth в Flutter/native передайте `code` из redirect URI. "
        "Ответ: `{ \"access_token\": \"...\", \"token_type\": \"bearer\" }`. "
        "Redirect URI при обмене должен совпадать с `AUTH_REDIRECT_URI` на сервере и с тем, "
        "что указан в запросе авторизации MSAL/AppAuth."
    ),
)
async def azure_exchange(body: AzureExchangeBody):
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{settings.auth_service_url.rstrip('/')}/auth/exchange",
                json={"code": body.code},
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail="Auth service unavailable") from e
    if r.status_code >= 400:
        detail = r.text or "Auth exchange failed"
        try:
            detail = r.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=r.status_code, detail=detail)
    return r.json()


def _clear_oauth_cookies(resp: RedirectResponse) -> None:
    resp.delete_cookie("oauth_state_nonce", path="/")


@router.get(
    "/login",
    summary="Azure Login",
    description=(
        "**Не вызывайте из Swagger «Try it out» / fetch / XHR:** ответ — цепочка 302 на другой хост, браузер заблокирует как CORS и покажет «Failed to fetch». "
        "Откройте URL вручную в новой вкладке или используйте кнопку входа во фронтенде."
    ),
)
async def azure_login():
    settings = get_settings()
    url = f"{settings.auth_service_url}/auth/login"
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
    description=(
        "Редирект на выход из Microsoft. Так же, как /login, не предназначен для вызова через fetch/Swagger — только переход по ссылке в браузере."
    ),
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


def _redirect_auth_failed(settings) -> RedirectResponse:
    base = (settings.frontend_url or "http://localhost").rstrip("/")
    resp = RedirectResponse(url=base + "/login?error=auth_failed", status_code=302)
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
        state_ok = parse_oauth_state_token(
            state,
            jwt_secret=settings.jwt_secret,
            jwt_algorithm=settings.jwt_algorithm or "HS256",
        )
        if not state_ok:
            nonce_ok = (request.cookies.get("oauth_state_nonce") or "").strip()
            if not (state and nonce_ok and state == nonce_ok):
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
            return _redirect_auth_failed(settings)
        try:
            data = r.json()
        except Exception as e:
            _log.warning("auth exchange returned non-JSON: %s", e)
            return _redirect_auth_failed(settings)
        access_token = data.get("access_token", "")

        base = (settings.frontend_url or "http://localhost").rstrip("/")
        callback_path = "/auth/callback"
        s = settings
        if s.auth_set_session_cookie and access_token:
            redirect_url = f"{base}{callback_path}?set_session=1"
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
