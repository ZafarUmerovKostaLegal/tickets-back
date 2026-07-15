from typing import Any, Optional

import httpx
import jwt
import msal

from infrastructure.config import get_settings


AZURE_LOGIN_SCOPES = ["email", "User.Read"]


def normalize_redirect_uri(value: object) -> str:
    """Strip noise; keep trailing '=' for msauth:// URIs; trim trailing / only for http(s)."""
    s = (str(value) if value is not None else "").strip().replace("\n", "").replace("\r", "")
    if not s:
        return ""
    if s.startswith(("http://", "https://")):
        return s.rstrip("/")
    return s


def allowed_redirect_uris(settings=None) -> set[str]:
    settings = settings or get_settings()
    out: set[str] = set()
    for raw in (
        getattr(settings, "auth_redirect_uri", None),
        getattr(settings, "auth_mobile_redirect_uri", None),
    ):
        u = normalize_redirect_uri(raw)
        if u:
            out.add(u)
    extra = (getattr(settings, "auth_redirect_uris_extra", None) or "").strip()
    if extra:
        for part in extra.split(","):
            u = normalize_redirect_uri(part)
            if u:
                out.add(u)
    return out


def resolve_exchange_redirect_uri(
    requested: str | None,
    *,
    settings=None,
) -> str | None:
    """Pick redirect_uri for token exchange; must be on allow-list."""
    settings = settings or get_settings()
    allowed = allowed_redirect_uris(settings)
    requested_n = normalize_redirect_uri(requested) if requested else ""
    if requested_n:
        if requested_n in allowed:
            return requested_n
        return None
    default = normalize_redirect_uri(settings.auth_redirect_uri)
    return default if default and default in allowed else (default or None)


def get_msal_app():
    settings = get_settings()
    return msal.ConfidentialClientApplication(
        settings.azure_client_id,
        authority=f"https://login.microsoftonline.com/{settings.azure_tenant_id}",
        client_credential=settings.azure_client_secret,
    )


def get_login_url(state: Optional[str] = None) -> str:
    settings = get_settings()
    app = get_msal_app()
    auth_url = app.get_authorization_request_url(
        scopes=AZURE_LOGIN_SCOPES,
        redirect_uri=normalize_redirect_uri(settings.auth_redirect_uri),
        state=state,
    )
    return auth_url


def get_logout_url(post_logout_redirect_uri: str) -> str:
    settings = get_settings()
    from urllib.parse import quote
    base = f"https://login.microsoftonline.com/{settings.azure_tenant_id}/oauth2/v2.0/logout"
    return f"{base}?post_logout_redirect_uri={quote(post_logout_redirect_uri)}"


def _claims_from_id_token(id_token: str | None) -> dict[str, Any] | None:
    if not id_token or not str(id_token).strip():
        return None
    try:
        claims = jwt.decode(
            str(id_token).strip(),
            options={"verify_signature": False, "verify_aud": False},
        )
    except Exception:
        return None
    return claims if isinstance(claims, dict) else None


def _acquire_token_pkce_public(
    code: str,
    redirect_uri: str,
    code_verifier: str,
    settings,
) -> Optional[dict]:
    """Native/public client exchange (Android MSAL) — no client_secret."""
    token_url = (
        f"https://login.microsoftonline.com/{settings.azure_tenant_id}/oauth2/v2.0/token"
    )
    # Scope is often optional on redeem; include app scopes + OIDC for id_token.
    scope = " ".join([*AZURE_LOGIN_SCOPES, "openid", "profile", "offline_access"])
    data = {
        "client_id": settings.azure_client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "scope": scope,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(token_url, data=data)
    except httpx.HTTPError:
        return None
    if r.status_code >= 400:
        return None
    try:
        payload = r.json()
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    claims = _claims_from_id_token(payload.get("id_token"))
    if not claims:
        return None
    out = dict(payload)
    out["id_token_claims"] = claims
    return out


def acquire_token_by_code(
    code: str,
    *,
    redirect_uri: str | None = None,
    code_verifier: str | None = None,
) -> Optional[dict]:
    settings = get_settings()
    ruri = resolve_exchange_redirect_uri(redirect_uri, settings=settings)
    if not ruri:
        return None

    verifier = (code_verifier or "").strip()
    if verifier:
        return _acquire_token_pkce_public(code, ruri, verifier, settings)

    app = get_msal_app()
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=AZURE_LOGIN_SCOPES,
        redirect_uri=ruri,
    )
    if "error" in result:
        return None
    return result


async def fetch_graph_profile_photo_download_url(access_token: str) -> Optional[str]:

    if not (access_token or "").strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://graph.microsoft.com/v1.0/me/photo",
                headers={"Authorization": f"Bearer {access_token.strip()}"},
            )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        data: dict[str, Any] = r.json()
    except (ValueError, TypeError):
        return None
    link = data.get("@microsoft.graph.downloadUrl")
    if isinstance(link, str) and link.strip():
        return link.strip()
    return None


async def resolve_profile_picture_from_tokens(tokens: dict, claims: dict) -> Optional[str]:

    pic = claims.get("picture")
    if isinstance(pic, str) and pic.strip():
        return pic.strip()
    access = tokens.get("access_token")
    if not access:
        return None
    return await fetch_graph_profile_photo_download_url(access)
