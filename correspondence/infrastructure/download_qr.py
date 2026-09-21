from __future__ import annotations

import time
from urllib.parse import quote

from infrastructure.config import Settings
from infrastructure.download_token import sign_download_token


def build_public_download_url(
    settings: Settings,
    *,
    document_id: str,
    attachment_id: str,
    token: str,
) -> str | None:
    base = (settings.public_api_base_url or "").strip().rstrip("/")
    if not base:
        return None
    return (
        f"{base}/api/v1/correspondence/{document_id}/attachments/{attachment_id}/public-file"
        f"?token={quote(token, safe='')}"
    )


def mint_download_qr(
    settings: Settings,
    *,
    document_id: str,
    attachment_id: str,
) -> dict:
    """Return { url, token, expiresAt } for QR embedding."""
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
    if not url:
        raise ValueError(
            "Задайте GATEWAY_BASE_URL / PUBLIC_API_BASE_URL для публичной ссылки QR"
        )
    return {
        "url": url,
        "token": token,
        "expiresAt": int(time.time()) + ttl,
        "attachmentId": attachment_id,
        "documentId": document_id,
    }
