"""Sign-in to Hub from the Kosta File Zoom App (Zoom in-client OAuth with PKCE).

The Zoom user must belong to the firm's Zoom account (ZOOM_ACCOUNT_ID): only then the e-mail is managed
by the firm and can be trusted to pick the Hub user.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from infrastructure.config import get_settings

TOKEN_URL = "https://zoom.us/oauth/token"
ME_URL = "https://api.zoom.us/v2/users/me"


class ZoomAuthError(Exception):
    def __init__(self, detail: str, status: int = 401):
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass
class ZoomIdentity:
    user_id: str
    email: str
    account_id: str
    display_name: str


def zoom_login_ready() -> bool:
    s = get_settings()
    return bool(s.zoom_account_id.strip() and s.zoom_app_client_id.strip() and s.zoom_app_client_secret.strip())


async def zoom_identity(code: str, code_verifier: str, *, transport: httpx.AsyncBaseTransport | None = None) -> ZoomIdentity:
    s = get_settings()
    if not zoom_login_ready():
        raise ZoomAuthError("Вход через Zoom не настроен на сервере", 503)
    async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
        token = await client.post(
            TOKEN_URL,
            auth=(s.zoom_app_client_id.strip(), s.zoom_app_client_secret.strip()),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.zoom_app_redirect_uri.strip(),
                "code_verifier": code_verifier,
            },
        )
        if token.status_code >= 400:
            raise ZoomAuthError("Zoom не подтвердил вход — попробуйте ещё раз")
        access = str(token.json().get("access_token") or "")
        if not access:
            raise ZoomAuthError("Zoom не выдал токен")
        me = await client.get(ME_URL, headers={"Authorization": f"Bearer {access}"})
    if me.status_code >= 400:
        raise ZoomAuthError("Zoom не отдал профиль пользователя")
    body = me.json()
    identity = ZoomIdentity(
        user_id=str(body.get("id") or ""),
        email=str(body.get("email") or "").strip().lower(),
        account_id=str(body.get("account_id") or ""),
        display_name=str(body.get("display_name") or "").strip(),
    )
    if identity.account_id != s.zoom_account_id.strip():
        raise ZoomAuthError("Войдите в Zoom рабочей учётной записью фирмы", 403)
    if not identity.email:
        raise ZoomAuthError("У учётной записи Zoom нет почты", 403)
    return identity
