from __future__ import annotations

import time
from urllib.parse import quote

from infrastructure.config import Settings
from infrastructure.download_token import sign_download_token


def build_public_download_path(
    *,
    document_id: str,
    token: str,
    attachment_id: str | None = None,
) -> str:
    """Relative API path for the public verification page (not a direct file download)."""
    if attachment_id:
        return (
            f"/api/v1/correspondence/{document_id}/attachments/{attachment_id}/public-card"
            f"?token={quote(token, safe='')}"
        )
    return (
        f"/api/v1/correspondence/{document_id}/public-card"
        f"?token={quote(token, safe='')}"
    )


def build_public_download_url(
    settings: Settings,
    *,
    document_id: str,
    token: str,
    attachment_id: str | None = None,
) -> str:
    """Absolute public URL for phone QR (requires GATEWAY_BASE_URL)."""
    path = build_public_download_path(
        document_id=document_id,
        attachment_id=attachment_id,
        token=token,
    )
    base = (settings.public_api_base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError(
            "Задайте GATEWAY_BASE_URL / PUBLIC_API_BASE_URL для публичной ссылки QR"
        )
    return f"{base}{path}"


def mint_download_qr(
    settings: Settings,
    *,
    document_id: str,
    attachment_id: str | None = None,
) -> dict:
    """Return { url, token, expiresAt } for QR embedding (document-scoped by default)."""
    secret = (settings.correspondence_download_token_secret or "").strip()
    if not secret:
        raise ValueError(
            "Задайте CORRESPONDENCE_DOWNLOAD_TOKEN_SECRET "
            "(или EXPENSE_EMAIL_ACTION_SECRET) на сервисе correspondence"
        )
    if len(secret) < 16:
        raise ValueError("CORRESPONDENCE_DOWNLOAD_TOKEN_SECRET слишком короткий (минимум 16 символов)")
    ttl = int(settings.correspondence_download_token_ttl_seconds)
    token = sign_download_token(
        secret,
        document_id=document_id,
        attachment_id=attachment_id,
        ttl_seconds=ttl,
    )
    url = build_public_download_url(
        settings,
        document_id=document_id,
        attachment_id=attachment_id,
        token=token,
    )
    return {
        "url": url,
        "token": token,
        "expiresAt": int(time.time()) + ttl,
        "attachmentId": attachment_id,
        "documentId": document_id,
    }
