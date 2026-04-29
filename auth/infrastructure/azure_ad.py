from typing import Any, Optional

import httpx
import msal

from infrastructure.config import get_settings


AZURE_LOGIN_SCOPES = ["email", "User.Read"]


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
        redirect_uri=settings.auth_redirect_uri,
        state=state,
    )
    return auth_url


def get_logout_url(post_logout_redirect_uri: str) -> str:
    settings = get_settings()
    from urllib.parse import quote
    base = f"https://login.microsoftonline.com/{settings.azure_tenant_id}/oauth2/v2.0/logout"
    return f"{base}?post_logout_redirect_uri={quote(post_logout_redirect_uri)}"


def acquire_token_by_code(code: str) -> Optional[dict]:
    settings = get_settings()
    app = get_msal_app()
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=AZURE_LOGIN_SCOPES,
        redirect_uri=settings.auth_redirect_uri,
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
