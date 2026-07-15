from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import RedirectResponse
from application.use_cases import (
    AzureLoginUseCase,
    GetCurrentUserUseCase,
    InvalidateSessionUseCase,
)
from application.ports import UserRepositoryPort, TokenServicePort, RoleRepositoryPort
from infrastructure.database import get_session
from infrastructure.repositories import UserRepository, RoleRepository
from infrastructure.jwt_service import JWTService
from infrastructure.azure_ad import (
    get_login_url,
    get_logout_url,
    acquire_token_by_code,
    resolve_profile_picture_from_tokens,
)
from infrastructure.oauth_state_jwt import create_oauth_state_token, parse_oauth_state_token
from backend_common.rbac_ui_permissions import build_ui_permissions
from domain.roles import Role
from infrastructure.config import get_settings
from presentation.http_auth import access_token_from_request
from presentation.schemas import (
    UserResponse,
    TokenResponse,
    RoleItem,
)

router = APIRouter(prefix="/auth", tags=["auth"])

OAUTH_STATE_COOKIE = "oauth_state_nonce"


def _samesite_cookie_value(raw: str) -> str:
    x = (raw or "lax").strip().lower()
    return x if x in ("lax", "strict", "none") else "lax"


def _apply_session_cookie(resp: Response, access_token: str) -> None:
    s = get_settings()
    if not s.auth_set_session_cookie:
        return
    resp.set_cookie(
        key=s.auth_session_cookie_name,
        value=access_token,
        max_age=int(s.jwt_expire_minutes * 60),
        httponly=True,
        secure=s.auth_session_cookie_secure,
        samesite=_samesite_cookie_value(s.auth_session_cookie_samesite),
        path="/",
    )


def _clear_session_cookie(resp: Response) -> None:
    s = get_settings()
    if not s.auth_set_session_cookie:
        return
    resp.delete_cookie(key=s.auth_session_cookie_name, path="/")


def _clear_oauth_cookies(response: RedirectResponse) -> None:
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/")


def _frontend_base(settings) -> str:
    return (settings.frontend_url or "http://localhost").rstrip("/")


def _error_redirect(settings, error_code: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{_frontend_base(settings)}/login?error={error_code}",
        status_code=302,
    )


def get_user_repo(session: AsyncSession = Depends(get_session)) -> UserRepositoryPort:
    return UserRepository(session)


def get_token_service() -> TokenServicePort:
    return JWTService()


def get_login_use_case(
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    token_service: TokenServicePort = Depends(get_token_service),
) -> AzureLoginUseCase:
    return AzureLoginUseCase(user_repo, token_service)


def get_current_user_use_case(
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    token_service: TokenServicePort = Depends(get_token_service),
) -> GetCurrentUserUseCase:
    return GetCurrentUserUseCase(user_repo, token_service)


def get_role_repo(session: AsyncSession = Depends(get_session)) -> RoleRepositoryPort:
    return RoleRepository(session)


@router.get("/roles")
async def list_roles(role_repo: RoleRepositoryPort = Depends(get_role_repo)):
    roles = await role_repo.list_all()
    return [RoleItem(value=r["name"], label=r["name"]) for r in roles]


@router.get("/login")
async def login():
    settings = get_settings()
    try:
        state_token = create_oauth_state_token(
            jwt_secret=settings.jwt_secret,
            jwt_algorithm=settings.jwt_algorithm,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return RedirectResponse(url=get_login_url(state=state_token), status_code=302)


@router.get("/logout")
async def logout():
    settings = get_settings()
    post_logout_redirect = f"{_frontend_base(settings)}/login"
    return RedirectResponse(url=get_logout_url(post_logout_redirect), status_code=302)


def _claims_to_user_and_token(claims: dict):
    azure_oid = claims.get("oid") or claims.get("sub") or ""
    email = claims.get("preferred_username") or claims.get("email") or ""
    display_name = claims.get("name")
    picture = claims.get("picture")
    return azure_oid, email, display_name, picture


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: Optional[str] = None,
    uc: AzureLoginUseCase = Depends(get_login_use_case),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    state_ok = parse_oauth_state_token(
        state,
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
    )
    if not state_ok:
        nonce_ok = (request.cookies.get(OAUTH_STATE_COOKIE) or "").strip()
        if not (state and nonce_ok and state == nonce_ok):
            resp = _error_redirect(settings, "oauth_state")
            _clear_oauth_cookies(resp)
            return resp

    tokens = acquire_token_by_code(code)
    if not tokens or "id_token_claims" not in tokens:
        resp = _error_redirect(settings, "auth_failed")
        _clear_oauth_cookies(resp)
        return resp
    claims = tokens["id_token_claims"]
    azure_oid, email, display_name, _ = _claims_to_user_and_token(claims)
    if not azure_oid or not email:
        resp = _error_redirect(settings, "missing_claims")
        _clear_oauth_cookies(resp)
        return resp
    picture = await resolve_profile_picture_from_tokens(tokens, claims)
    user, access_token = await uc.execute(
        azure_oid, email, display_name, picture, Role.EMPLOYEE.value
    )
    await session.commit()
    redirect_base = _frontend_base(settings)
    callback_path = "/auth/callback"
    if settings.auth_set_session_cookie:
        redirect_url = f"{redirect_base}{callback_path}?set_session=1"
    else:
        redirect_url = f"{redirect_base}{callback_path}#access_token={access_token}"
    resp = RedirectResponse(
        url=redirect_url,
        status_code=302,
    )
    _apply_session_cookie(resp, access_token)
    _clear_oauth_cookies(resp)
    return resp


@router.post("/exchange", response_model=TokenResponse)
async def exchange(
    body: dict,
    response: Response,
    uc: AzureLoginUseCase = Depends(get_login_use_case),
    session: AsyncSession = Depends(get_session),
):
    code = body.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="code required")
    redirect_uri = body.get("redirect_uri") or body.get("redirectUri")
    code_verifier = body.get("code_verifier") or body.get("codeVerifier")
    tokens = acquire_token_by_code(
        code,
        redirect_uri=str(redirect_uri).strip() if redirect_uri else None,
        code_verifier=str(code_verifier).strip() if code_verifier else None,
    )
    if not tokens or "id_token_claims" not in tokens:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    claims = tokens["id_token_claims"]
    azure_oid, email, display_name, _ = _claims_to_user_and_token(claims)
    if not azure_oid or not email:
        raise HTTPException(status_code=400, detail="Missing user claims")
    picture = await resolve_profile_picture_from_tokens(tokens, claims)
    user, access_token = await uc.execute(
        azure_oid, email, display_name, picture, Role.EMPLOYEE.value
    )
    await session.commit()
    _apply_session_cookie(response, access_token)
    return TokenResponse(access_token=access_token)


@router.post("/logout", status_code=204)
async def logout_invalidate_session(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    token_service: TokenServicePort = Depends(get_token_service),
    session: AsyncSession = Depends(get_session),
):
    token = access_token_from_request(request, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Authorization required")
    payload = token_service.decode_token(token)
    uc = GetCurrentUserUseCase(user_repo, token_service)
    user = await uc.execute(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    token_jti = payload.get("jti") if payload else None
    logout_jti = token_jti.strip() if isinstance(token_jti, str) and token_jti.strip() else None
    await InvalidateSessionUseCase(user_repo).execute(user.id, logout_jti)
    await session.commit()
    resp = Response(status_code=204)
    _clear_session_cookie(resp)
    return resp


@router.get("/me", response_model=UserResponse)
async def me(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    uc: GetCurrentUserUseCase = Depends(get_current_user_use_case),
):
    token = access_token_from_request(request, authorization)
    user = await uc.execute(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        picture=user.picture,
        role=user.role,
        is_blocked=user.is_blocked,
        is_archived=user.is_archived,
        created_at=user.created_at,
        updated_at=user.updated_at,
        permissions=build_ui_permissions(user.role, user.time_tracking_role, user.position),
        time_tracking_role=user.time_tracking_role,
    )
