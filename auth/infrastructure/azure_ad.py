from __future__ import annotations

import threading
from typing import Any, Optional

import httpx
import jwt
import msal
from jwt import PyJWKClient

from infrastructure.config import get_settings


AZURE_LOGIN_SCOPES = ["email", "User.Read"]

# Microsoft Graph audiences commonly seen on User.Read access tokens.
_GRAPH_AUDIENCES = frozenset(
    {
        "https://graph.microsoft.com",
        "00000003-0000-0000-c000-000000000000",
    }
)

_jwks_lock = threading.Lock()
_jwks_clients: dict[str, PyJWKClient] = {}


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
        getattr(settings, "auth_outlook_redirect_uri", None),
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


class AzureTokenExchangeError(Exception):
    """Azure token endpoint / allow-list failure with a client-safe message."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def azure_issuer_values(tenant_id: str) -> tuple[str, ...]:
    tid = (tenant_id or "").strip()
    if not tid:
        return ()
    return (
        f"https://login.microsoftonline.com/{tid}/v2.0",
        f"https://sts.windows.net/{tid}/",
    )


def _get_jwks_client(tenant_id: str) -> PyJWKClient:
    tid = (tenant_id or "").strip()
    if not tid:
        raise AzureTokenExchangeError("Azure tenant is not configured")
    with _jwks_lock:
        client = _jwks_clients.get(tid)
        if client is None:
            client = PyJWKClient(
                f"https://login.microsoftonline.com/{tid}/discovery/v2.0/keys",
                cache_keys=True,
                lifespan=3600,
            )
            _jwks_clients[tid] = client
        return client


def _normalize_audiences(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        v = raw.strip()
        return {v} if v else set()
    if isinstance(raw, (list, tuple, set)):
        out: set[str] = set()
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.add(item.strip())
        return out
    return set()


def _assert_tenant_and_issuer(claims: dict[str, Any], *, tenant_id: str) -> None:
    tid = str(claims.get("tid") or "").strip()
    if not tid or tid.lower() != tenant_id.strip().lower():
        raise AzureTokenExchangeError("Azure token tenant mismatch")
    issuers = set(azure_issuer_values(tenant_id))
    iss = str(claims.get("iss") or "").strip()
    if iss not in issuers:
        raise AzureTokenExchangeError("Azure token issuer mismatch")


def _assert_token_app(claims: dict[str, Any], *, client_id: str) -> None:
    """When azp/appid present, it must be our application."""
    app = str(claims.get("azp") or claims.get("appid") or "").strip()
    if app and app != client_id:
        raise AzureTokenExchangeError("Azure token application mismatch")


def verify_azure_id_token(id_token: str | None, *, settings=None) -> dict[str, Any]:
    """
    Cryptographically verify Azure AD id_token (sig, aud, iss, tid, exp).
    Never use unverified JWT claims for identity.
    """
    settings = settings or get_settings()
    token = (id_token or "").strip()
    if not token:
        raise AzureTokenExchangeError("id_token required")
    tenant_id = (settings.azure_tenant_id or "").strip()
    client_id = (settings.azure_client_id or "").strip()
    if not tenant_id or not client_id:
        raise AzureTokenExchangeError("Azure AD is not configured")

    try:
        signing_key = _get_jwks_client(tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=list(azure_issuer_values(tenant_id)),
            options={
                "require": ["exp", "iat", "iss", "aud"],
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
            },
        )
    except AzureTokenExchangeError:
        raise
    except Exception as exc:
        raise AzureTokenExchangeError("Azure id_token invalid") from exc

    if not isinstance(claims, dict):
        raise AzureTokenExchangeError("Azure id_token invalid")
    _assert_tenant_and_issuer(claims, tenant_id=tenant_id)
    _assert_token_app(claims, client_id=client_id)
    return claims


def verify_azure_access_token(access_token: str | None, *, settings=None) -> dict[str, Any]:
    """
    Verify Azure access token signature + tenant/issuer.
    Audience may be Graph or our API/client id (SPA bootstrap uses Graph).
    """
    settings = settings or get_settings()
    token = (access_token or "").strip()
    if not token:
        raise AzureTokenExchangeError("access_token required")
    tenant_id = (settings.azure_tenant_id or "").strip()
    client_id = (settings.azure_client_id or "").strip()
    if not tenant_id or not client_id:
        raise AzureTokenExchangeError("Azure AD is not configured")

    try:
        signing_key = _get_jwks_client(tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=list(azure_issuer_values(tenant_id)),
            options={
                "require": ["exp", "iat", "iss"],
                "verify_aud": False,
                "verify_iss": True,
                "verify_exp": True,
            },
        )
    except AzureTokenExchangeError:
        raise
    except Exception as exc:
        raise AzureTokenExchangeError("Azure access_token invalid") from exc

    if not isinstance(claims, dict):
        raise AzureTokenExchangeError("Azure access_token invalid")
    _assert_tenant_and_issuer(claims, tenant_id=tenant_id)
    _assert_token_app(claims, client_id=client_id)

    allowed_aud = set(_GRAPH_AUDIENCES) | {
        client_id,
        f"api://{client_id}",
    }
    auds = _normalize_audiences(claims.get("aud"))
    if not auds or auds.isdisjoint(allowed_aud):
        raise AzureTokenExchangeError("Azure access_token audience mismatch")
    return claims


def _claims_from_id_token(id_token: str | None) -> dict[str, Any] | None:
    """Backward-compatible helper — always verifies signature and tenant claims."""
    try:
        return verify_azure_id_token(id_token)
    except AzureTokenExchangeError:
        return None


def _acquire_token_pkce_public(
    code: str,
    redirect_uri: str,
    code_verifier: str,
    settings,
) -> dict:
    """SPA / public client exchange (Outlook add-in PKCE) — no client_secret."""
    token_url = (
        f"https://login.microsoftonline.com/{settings.azure_tenant_id}/oauth2/v2.0/token"
    )
    data = {
        "client_id": settings.azure_client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(token_url, data=data)
    except httpx.HTTPError as exc:
        raise AzureTokenExchangeError("Azure token endpoint unreachable") from exc
    try:
        payload = r.json()
    except Exception as exc:
        raise AzureTokenExchangeError(f"Azure token response invalid (HTTP {r.status_code})") from exc
    if not isinstance(payload, dict):
        raise AzureTokenExchangeError("Azure token response invalid")
    if r.status_code >= 400 or payload.get("error"):
        desc = (
            payload.get("error_description")
            or payload.get("error")
            or f"HTTP {r.status_code}"
        )
        raise AzureTokenExchangeError(str(desc))
    claims = verify_azure_id_token(payload.get("id_token"), settings=settings)
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
        raise AzureTokenExchangeError(
            "redirect_uri not allow-listed on auth service "
            "(set AUTH_OUTLOOK_REDIRECT_URI to the add-in callback)"
        )

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
        desc = result.get("error_description") or result.get("error") or "token exchange failed"
        raise AzureTokenExchangeError(str(desc))
    # Re-verify id_token locally (sig/aud/tid/iss) even when MSAL decoded claims.
    raw_id = result.get("id_token")
    if not raw_id:
        raise AzureTokenExchangeError("Azure id_token missing or invalid")
    out = dict(result)
    out["id_token_claims"] = verify_azure_id_token(raw_id, settings=settings)
    return out


async def fetch_graph_me(access_token: str) -> dict[str, Any]:
    """Validate an Azure SPA access token via Microsoft Graph /me."""
    token = (access_token or "").strip()
    if not token:
        raise AzureTokenExchangeError("access_token required")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                "https://graph.microsoft.com/v1.0/me"
                "?$select=id,displayName,mail,userPrincipalName",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise AzureTokenExchangeError("Microsoft Graph unreachable") from exc
    if r.status_code >= 400:
        raise AzureTokenExchangeError(
            f"Microsoft Graph rejected token (HTTP {r.status_code})"
        )
    try:
        data = r.json()
    except Exception as exc:
        raise AzureTokenExchangeError("Microsoft Graph response invalid") from exc
    if not isinstance(data, dict):
        raise AzureTokenExchangeError("Microsoft Graph response invalid")
    return data


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
