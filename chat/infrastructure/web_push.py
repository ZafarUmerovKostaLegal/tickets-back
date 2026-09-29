from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.models import ROOM_TYPE_DM
from infrastructure.repositories import ChatRepository

_log = logging.getLogger("chat.web_push")
_PREVIEW_MAX = 180


def chat_push_preview(
    *,
    body: str,
    message_kind: str,
    has_file: bool = False,
    content_type: str | None = None,
) -> str:
    kind = (message_kind or "text").strip()
    text = " ".join((body or "").split())
    if kind == "checklist":
        return (f"Чеклист: {text}" if text else "Чеклист")[:_PREVIEW_MAX]
    if kind == "quiz":
        return (f"Викторина: {text}" if text else "Викторина")[:_PREVIEW_MAX]
    if kind == "poll":
        return (f"Опрос: {text}" if text else "Опрос")[:_PREVIEW_MAX]
    if has_file and not text:
        if (content_type or "").lower().startswith("image/"):
            return "Фото"
        return "Файл"
    return (text or "Новое сообщение")[:_PREVIEW_MAX]


def chat_push_title_and_body(
    *,
    room_type: str,
    room_title: str,
    sender_name: str,
    preview: str,
) -> tuple[str, str]:
    sender = sender_name.strip() or "Коллега"
    text = preview.strip() or "Новое сообщение"
    if room_type == ROOM_TYPE_DM:
        return sender, text
    title = room_title.strip() or "Kosta Daily"
    return title, f"{sender}: {text}"


def _b64url_decode(value: str) -> bytes:
    padded = value.strip() + "=" * (-len(value.strip()) % 4)
    return base64.urlsafe_b64decode(padded)


def vapid_private_key_pem(raw_b64: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    raw = _b64url_decode(raw_b64)
    if len(raw) != 32:
        raise ValueError("VAPID private key must be 32 bytes")
    key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def _vapid_ready() -> tuple[str, str, str] | None:
    settings = get_settings()
    public_key = (settings.chat_vapid_public_key or "").strip()
    private_key = (settings.chat_vapid_private_key or "").strip()
    subject = (settings.chat_vapid_subject or "").strip() or "mailto:notifications@kostalegal.com"
    if not public_key or not private_key:
        return None
    return public_key, private_key, subject


async def _sender_profile(user_id: int) -> tuple[str, str | None]:
    settings = get_settings()
    secret = (settings.ws_internal_secret or "").strip()
    base = (settings.auth_service_url or "").rstrip("/")
    if not secret or not base:
        return "", None
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(
                f"{base}/internal/users/by-ids",
                params={"ids": str(int(user_id))},
                headers={"X-Internal-Key": secret},
            )
        if response.status_code >= 400:
            return "", None
        row = response.json().get(str(int(user_id))) or {}
        name = str(row.get("display_name") or "").strip()
        picture = str(row.get("picture") or "").strip()
        if not picture.startswith("https://"):
            picture = ""
        return name, picture or None
    except Exception as exc:
        _log.warning("chat push profile lookup failed: %r", exc)
        return "", None


def _send_one(subscription: dict[str, Any], payload: str, private_pem: str, subject: str) -> int | None:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=private_pem,
            vapid_claims={"sub": subject},
            ttl=60 * 60 * 12,
            timeout=8,
        )
        return None
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return int(status)
        _log.warning("web push failed status=%s endpoint=%s", status, subscription.get("endpoint", "")[:80])
        return None
    except Exception as exc:
        _log.warning("web push request failed: %r", exc)
        return None


async def deliver_chat_browser_push(
    session: AsyncSession,
    *,
    room_id: int,
    author_user_id: int,
    body: str,
    message_kind: str,
    has_file: bool = False,
    content_type: str | None = None,
) -> None:
    ready = _vapid_ready()
    if ready is None:
        return
    try:
        await _deliver(session, ready, room_id=room_id, author_user_id=author_user_id, body=body, message_kind=message_kind, has_file=has_file, content_type=content_type)
    except Exception as exc:
        _log.warning("chat browser push skipped: %r", exc)


async def _deliver(
    session: AsyncSession,
    ready: tuple[str, str, str],
    *,
    room_id: int,
    author_user_id: int,
    body: str,
    message_kind: str,
    has_file: bool,
    content_type: str | None,
) -> None:
    _public_key, private_key, subject = ready
    repo = ChatRepository(session)
    room = await repo.get_room(room_id)
    if room is None:
        return
    recipients = [uid for uid in await repo.member_user_ids(room_id) if uid != author_user_id]
    subscriptions = await repo.push_subscriptions_for_users(recipients)
    if not subscriptions:
        return
    sender_name, picture = await _sender_profile(author_user_id)
    preview = chat_push_preview(
        body=body,
        message_kind=message_kind,
        has_file=has_file,
        content_type=content_type,
    )
    title, text = chat_push_title_and_body(
        room_type=room.room_type,
        room_title=room.title,
        sender_name=sender_name,
        preview=preview,
    )
    payload = json.dumps(
        {
            "title": title,
            "body": text,
            "image": picture,
            "roomId": int(room_id),
            "url": f"/kosta-daily?room={int(room_id)}",
        },
        ensure_ascii=False,
    )
    try:
        private_pem = vapid_private_key_pem(private_key)
    except Exception as exc:
        _log.warning("vapid private key is invalid: %r", exc)
        return

    async def _one(row) -> str | None:
        status = await asyncio.to_thread(
            _send_one,
            {
                "endpoint": row.endpoint,
                "keys": {"p256dh": row.p256dh, "auth": row.auth},
            },
            payload,
            private_pem,
            subject,
        )
        if status in (404, 410):
            return row.endpoint
        return None

    expired = await asyncio.gather(*(_one(row) for row in subscriptions))
    stale = [endpoint for endpoint in expired if endpoint]
    for endpoint in stale:
        await repo.delete_push_subscription_by_endpoint(endpoint)
    if stale:
        await session.commit()
