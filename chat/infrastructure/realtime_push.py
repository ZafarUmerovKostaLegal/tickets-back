from __future__ import annotations

import logging
from typing import Any

import httpx

from infrastructure.config import get_settings

_log = logging.getLogger("chat.realtime_push")


async def push_chat_event(
    *,
    recipient_user_ids: list[int],
    room_id: int,
    event: str,
    payload: dict[str, Any],
) -> None:
    settings = get_settings()
    url = (settings.chat_push_url or "").strip()
    secret = (settings.ws_internal_secret or "").strip()
    if not url or not secret or not recipient_user_ids:
        return
    body = {
        "recipient_user_ids": [int(x) for x in recipient_user_ids],
        "room_id": int(room_id),
        "event": event,
        "payload": payload,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(url, json=body, headers={"X-Internal-Key": secret})
        if r.status_code >= 400:
            _log.warning(
                "chat push failed status=%s body=%s",
                r.status_code,
                (r.text or "")[:500],
            )
    except Exception as exc:
        _log.warning("chat push request failed: %r", exc)
