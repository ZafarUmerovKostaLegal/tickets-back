from __future__ import annotations

import logging

import httpx

from infrastructure.config import Settings

_log = logging.getLogger(__name__)


async def send_system_notification(
    settings: Settings,
    *,
    recipient_user_id: int,
    title: str,
    description: str,
    notification_type: str = "correspondence",
) -> None:
    url = (settings.notification_push_url or "").strip()
    secret = (settings.ws_internal_secret or "").strip()
    if not url or not secret:
        _log.warning(
            "correspondence notify skipped: NOTIFICATION_PUSH_URL/WS_INTERNAL_SECRET not configured"
        )
        return
    body = {
        "recipient_user_id": int(recipient_user_id),
        "title": title[:500],
        "description": description[:2000],
        "notification_type": (notification_type or "correspondence")[:64],
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, json=body, headers={"X-Internal-Key": secret})
        if response.status_code >= 400:
            _log.warning(
                "correspondence notify failed recipient=%s status=%s body=%s",
                recipient_user_id,
                response.status_code,
                (response.text or "")[:500],
            )
    except Exception:
        _log.exception("correspondence notify error recipient=%s", recipient_user_id)
