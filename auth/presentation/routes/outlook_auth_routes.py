from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application.errors import UserAccessDenied
from application.outlook_tokens import OutlookTokenService
from application.ports import UserRepositoryPort
from application.use_cases import GetCurrentUserUseCase, assert_user_can_authenticate
from infrastructure.azure_ad import (
    AzureTokenExchangeError,
    acquire_token_by_code,
    resolve_profile_picture_from_tokens,
    verify_office_sso_token,
)
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.jwt_service import JWTService
from infrastructure.repositories import UserRepository
from presentation.http_auth import access_token_from_request
from application.use_cases import AzureLoginUseCase
from domain.roles import Role
from presentation.routes.auth_routes import _apply_session_cookie, _claims_to_user_and_token, _remember_device, get_login_use_case
from presentation.schemas import OutlookExchangeBody, OutlookRefreshBody, OutlookTokenResponse, TokenResponse, UserResponse
from backend_common.rbac_ui_permissions import build_ui_permissions

router = APIRouter(prefix="/auth/outlook", tags=["auth-outlook"])

DEVICE_HEADER = "X-Outlook-Device-Id"


def get_user_repo(session: AsyncSession = Depends(get_session)) -> UserRepositoryPort:
    return UserRepository(session)


def get_outlook_tokens(
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
) -> OutlookTokenService:
    return OutlookTokenService(session, user_repo, JWTService())


def _pair_response(pair) -> OutlookTokenResponse:
    return OutlookTokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type=pair.token_type,
        expires_in=pair.expires_in,
        scope=pair.scope,
    )


@router.get("/login-url")
async def outlook_login_url(
    redirect_uri: str | None = None,
    state: str | None = None,
    prompt: str | None = None,
    login_hint: str | None = None,
):
    """Return Azure authorize URL for the add-in (public client + PKCE on client)."""
    from urllib.parse import urlencode

    settings = get_settings()
    ruri = (redirect_uri or settings.auth_outlook_redirect_uri or "").strip().rstrip("/")
    allowed = {
        (settings.auth_outlook_redirect_uri or "").strip().rstrip("/"),
        *[
            p.strip().rstrip("/")
            for p in (settings.auth_redirect_uris_extra or "").split(",")
            if p.strip()
        ],
    }
    if ruri not in allowed:
        raise HTTPException(status_code=400, detail="redirect_uri not allow-listed")
    params: dict[str, str] = {
        "client_id": settings.azure_client_id,
        "response_type": "code",
        "redirect_uri": ruri,
        "response_mode": "query",
        # Mail.Read — full conversation export from Outlook add-in via Microsoft Graph
        "scope": "openid profile email offline_access User.Read Mail.Read",
        "state": state or "outlook",
    }
    prompt_n = (prompt or "").strip()
    if prompt_n:
        params["prompt"] = prompt_n
    hint = (login_hint or "").strip()
    if hint:
        params["login_hint"] = hint
    base = f"https://login.microsoftonline.com/{settings.azure_tenant_id}/oauth2/v2.0/authorize"
    return {
        "authorizeUrl": f"{base}?{urlencode(params)}",
        "redirectUri": ruri,
        "clientId": settings.azure_client_id,
        "tenantId": settings.azure_tenant_id,
    }


@router.post("/exchange", response_model=OutlookTokenResponse)
async def outlook_exchange(
    body: OutlookExchangeBody,
    session: AsyncSession = Depends(get_session),
    tokens: OutlookTokenService = Depends(get_outlook_tokens),
):
    settings = get_settings()
    redirect = (body.redirect_uri or settings.auth_outlook_redirect_uri or "").strip()
    try:
        azure = acquire_token_by_code(
            body.code,
            redirect_uri=redirect,
            code_verifier=body.code_verifier,
        )
    except AzureTokenExchangeError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    if not azure or "id_token_claims" not in azure:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    claims = azure["id_token_claims"]
    azure_oid, email, display_name, _ = _claims_to_user_and_token(claims)
    if not azure_oid or not email:
        raise HTTPException(status_code=400, detail="Missing user claims")
    picture = await resolve_profile_picture_from_tokens(azure, claims)
    user = await tokens.ensure_user(
        azure_oid=azure_oid,
        email=email,
        display_name=display_name,
        picture=picture,
    )
    if user.is_blocked or user.is_archived:
        raise HTTPException(status_code=403, detail="User blocked or archived")
    try:
        pair = await tokens.issue_for_user(user, device_id=body.device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return _pair_response(pair)


class SsoBody(BaseModel):
    token: str = Field(min_length=20, max_length=12000)


@router.post("/sso", response_model=TokenResponse)
async def office_sso(
    body: SsoBody,
    request: Request,
    response: Response,
    uc: AzureLoginUseCase = Depends(get_login_use_case),
    session: AsyncSession = Depends(get_session),
):
    """Office / Teams single sign-on: the host vouches for the signed-in work account."""
    try:
        claims = verify_office_sso_token(body.token)
    except AzureTokenExchangeError as exc:
        raise HTTPException(status_code=401, detail=exc.detail) from exc
    email = str(
        claims.get("preferred_username") or claims.get("upn") or claims.get("email") or claims.get("unique_name") or ""
    ).strip()
    if not email:
        raise HTTPException(status_code=400, detail="Missing email in SSO token")
    display_name = str(claims.get("name") or email).strip() or None
    try:
        _, hub_token = await uc.execute(str(claims["oid"]).strip(), email, display_name, None, Role.EMPLOYEE.value)
    except UserAccessDenied as exc:
        raise HTTPException(status_code=403, detail="User blocked or archived") from exc
    await _remember_device(request, hub_token, session)
    await session.commit()
    _apply_session_cookie(response, hub_token)
    return TokenResponse(access_token=hub_token)


@router.post("/refresh", response_model=OutlookTokenResponse)
async def outlook_refresh(
    body: OutlookRefreshBody,
    session: AsyncSession = Depends(get_session),
    tokens: OutlookTokenService = Depends(get_outlook_tokens),
    x_outlook_device_id: Optional[str] = Header(None, alias=DEVICE_HEADER),
):
    device = (x_outlook_device_id or body.device_id or "").strip()
    if device != body.device_id.strip():
        raise HTTPException(status_code=401, detail="device mismatch")
    try:
        pair = await tokens.refresh(body.refresh_token, device_id=device)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    await session.commit()
    return _pair_response(pair)


@router.post("/logout", status_code=204)
async def outlook_logout(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    tokens: OutlookTokenService = Depends(get_outlook_tokens),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_outlook_device_id: Optional[str] = Header(None, alias=DEVICE_HEADER),
):
    raw = access_token_from_request(request, authorization)
    if not raw:
        raise HTTPException(status_code=401, detail="Authorization required")
    payload = JWTService().decode_token(raw) or {}
    user = await GetCurrentUserUseCase(user_repo, JWTService()).execute(
        raw, device_id=x_outlook_device_id or payload.get("did")
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    family = str(payload.get("fid") or "").strip() or None
    await tokens.logout(user_id=user.id, device_id=x_outlook_device_id, family_id=family)
    await session.commit()
    return None


@router.get("/me", response_model=UserResponse)
async def outlook_me(
    request: Request,
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_outlook_device_id: Optional[str] = Header(None, alias=DEVICE_HEADER),
):
    raw = access_token_from_request(request, authorization)
    payload = JWTService().decode_token(raw) or {}
    if payload.get("typ") != "outlook_fm":
        raise HTTPException(status_code=401, detail="Outlook token required")
    user = await GetCurrentUserUseCase(user_repo, JWTService()).execute(
        raw, device_id=x_outlook_device_id
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        picture=user.picture,
        role=user.role,
        position=user.position,
        is_blocked=user.is_blocked,
        is_archived=user.is_archived,
        created_at=user.created_at,
        updated_at=user.updated_at,
        permissions=build_ui_permissions(user.role, user.time_tracking_role, user.position),
        time_tracking_role=user.time_tracking_role,
        desktop_background=user.desktop_background,
        initials=user.initials,
        token_typ="outlook_fm",
        token_scope=str(payload.get("scope") or ""),
    )


def _pairing(session: AsyncSession = Depends(get_session)):
    from application.outlook_pairing import OutlookPairingService

    return OutlookPairingService(session)


async def _hub_user(request: Request, authorization: Optional[str], user_repo: UserRepositoryPort):
    raw = access_token_from_request(request, authorization)
    user = await GetCurrentUserUseCase(user_repo, JWTService()).execute(raw) if raw else None
    if not user:
        raise HTTPException(status_code=401, detail="Войдите в Kosta Hub")
    return user


class PairCodeBody(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class PairPollBody(BaseModel):
    pairId: str = Field(min_length=8, max_length=64)
    secret: str = Field(min_length=16, max_length=128)


@router.post("/pair/start")
async def pair_start(request: Request, session: AsyncSession = Depends(get_session), pairing=Depends(_pairing)):
    """Add-in asks for a one-time code; the user approves it in Kosta Hub on any device."""
    started = await pairing.start(request.headers.get("user-agent", ""))
    await session.commit()
    hub = (get_settings().frontend_url or "").strip().rstrip("/")
    code = started.code.replace("-", "")
    return {
        "pairId": started.pair_id,
        "secret": started.secret,
        "code": started.code,
        "expiresIn": started.expires_in,
        "interval": 2,
        "verifyUrl": f"{hub}/outlook-pair?code={code}" if hub else "",
    }


@router.get("/pair/info")
async def pair_info(
    code: str,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    pairing=Depends(_pairing),
):
    from application.outlook_pairing import PairingNotFound

    await _hub_user(request, authorization, user_repo)
    try:
        info = await pairing.info(code)
    except PairingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": info.code, "device": info.device, "expiresIn": info.expires_in, "confirmed": info.confirmed}


@router.post("/pair/confirm", status_code=204)
async def pair_confirm(
    body: PairCodeBody,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    pairing=Depends(_pairing),
):
    from application.outlook_pairing import PairingNotFound

    user = await _hub_user(request, authorization, user_repo)
    try:
        await pairing.confirm(body.code, user.id)
    except PairingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return None


@router.post("/pair/poll")
async def pair_poll(
    body: PairPollBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    pairing=Depends(_pairing),
):
    from application.outlook_pairing import PairingNotFound

    try:
        user_id = await pairing.poll(body.pairId, body.secret)
    except PairingNotFound as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    if user_id is None:
        return {"status": "pending"}
    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=410, detail="Пользователь не найден")
    try:
        assert_user_can_authenticate(user)
    except UserAccessDenied as exc:
        raise HTTPException(status_code=403, detail="User blocked or archived") from exc
    jti = str(uuid.uuid4())
    await user_repo.register_session_jti(user.id, jti, max_sessions=get_settings().auth_max_concurrent_sessions)
    token = JWTService().create_access_token(user.id, user.azure_oid, jti)
    await _remember_device(request, token, session)
    await session.commit()
    return {"status": "ok", "access_token": token, "token_type": "bearer"}
