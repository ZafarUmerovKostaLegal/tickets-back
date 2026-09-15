from __future__ import annotations

import logging

import httpx

from infrastructure.config import Settings

_log = logging.getLogger(__name__)


def _candidate_notify_urls(settings: Settings) -> list[str]:
    urls: list[str] = []
    primary = (settings.notification_push_url or "").strip()
    if primary:
        urls.append(primary)
    service = (getattr(settings, "notifications_service_url", None) or "").strip().rstrip("/")
    if service:
        direct = f"{service}/notifications/system"
        if direct not in urls:
            urls.append(direct)
    return urls


async def send_system_notification(
    settings: Settings,
    *,
    recipient_user_id: int,
    title: str,
    description: str,
    notification_type: str = "correspondence",
) -> None:
    secret = (settings.ws_internal_secret or "").strip()
    urls = _candidate_notify_urls(settings)
    if not urls:
        _log.error(
            "correspondence notify skipped: NOTIFICATION_PUSH_URL / NOTIFICATIONS_SERVICE_URL not configured "
            "recipient=%s type=%s",
            recipient_user_id,
            notification_type,
        )
        return
    if not secret:
        _log.error(
            "correspondence notify skipped: WS_INTERNAL_SECRET empty "
            "(must match gateway/notifications) recipient=%s type=%s",
            recipient_user_id,
            notification_type,
        )
        return
    body = {
        "recipient_user_id": int(recipient_user_id),
        "title": title[:500],
        "description": description[:2000],
        "notification_type": (notification_type or "correspondence")[:64],
    }
    headers = {"X-Internal-Key": secret}
    last_error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for url in urls:
                try:
                    response = await client.post(url, json=body, headers=headers)
                except Exception as exc:
                    last_error = f"{url}: {exc}"
                    _log.warning(
                        "correspondence notify request error url=%s recipient=%s err=%s",
                        url,
                        recipient_user_id,
                        exc,
                    )
                    continue
                if response.status_code < 400:
                    return
                last_error = f"{url}: HTTP {response.status_code} {(response.text or '')[:300]}"
                _log.warning(
                    "correspondence notify failed url=%s recipient=%s status=%s body=%s",
                    url,
                    recipient_user_id,
                    response.status_code,
                    (response.text or "")[:500],
                )
    except Exception:
        _log.exception("correspondence notify error recipient=%s", recipient_user_id)
        return
    if last_error:
        _log.error(
            "correspondence notify all endpoints failed recipient=%s type=%s last=%s",
            recipient_user_id,
            notification_type,
            last_error,
        )
