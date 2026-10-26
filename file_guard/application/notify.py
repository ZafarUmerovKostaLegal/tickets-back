from __future__ import annotations

import logging

import httpx

from infrastructure.config import get_settings

logger = logging.getLogger("file_guard.notify")


def _plain(value: str, limit: int) -> str:
    cleaned = "".join(ch if ch.isprintable() else " " for ch in (value or ""))
    return " ".join(cleaned.split())[:limit]


async def notify_file_attempt(*, windows_user: str, host: str, file_name: str, resource: str) -> None:
    settings = get_settings()
    secret = (settings.ws_internal_secret or "").strip()
    if not secret:
        return
    emails = [part.strip() for part in (settings.notify_emails or "").split(",") if part.strip()]
    if not emails:
        return
    title = "Попытка отправить файл"
    who = _plain(windows_user or host or "Пользователь", 200)
    description = _plain(
        f"{who} на {host}: «{file_name or 'файл'}» → {resource or 'приложение'}",
        500,
    )
    headers = {"X-Internal-Key": secret}
    auth_base = settings.auth_service_url.rstrip("/")
    notes_base = settings.notifications_service_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for email in emails:
                user_res = await client.get(
                    f"{auth_base}/internal/users/by-email",
                    params={"email": email},
                    headers=headers,
                )
                if user_res.status_code != 200:
                    continue
                user_id = user_res.json().get("id")
                if not isinstance(user_id, int):
                    continue
                await client.post(
                    f"{notes_base}/notifications/system",
                    headers=headers,
                    json={
                        "title": title,
                        "description": description,
                        "recipient_user_id": user_id,
                        "notification_type": "file_guard",
                    },
                )
    except Exception:
        logger.warning("file guard notification was not delivered")
