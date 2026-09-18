from __future__ import annotations

import logging

import httpx

from infrastructure.config import Settings

_log = logging.getLogger(__name__)

TICKET_STATUS_ON_APPROVAL = "На согласовании"
TICKET_STATUS_IN_PROGRESS = "В работе"
TICKET_STATUS_OPEN = "Открыт"


def _normalize_role_key(role: str | None) -> str:
    return (role or "").strip().casefold().replace("ё", "е")


def is_partner_org_role(role: str | None, position: str | None = None) -> bool:
    kr = _normalize_role_key(role)
    if "партнер" in kr or "partner" in kr:
        return True
    kp = _normalize_role_key(position)
    return "партнер" in kp or "partner" in kp


async def fetch_auth_user_public(
    settings: Settings,
    authorization: str | None,
    user_id: int,
) -> dict | None:
    if not authorization or not authorization.strip():
        return None
    hdr = authorization.strip()
    if not hdr.lower().startswith("bearer "):
        hdr = f"Bearer {hdr}"
    base = (settings.auth_service_url or "").rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{base}/users/{user_id}/public",
                headers={"Authorization": hdr},
            )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return data
    except httpx.RequestError as exc:
        _log.warning("auth user public fetch failed user_id=%s err=%s", user_id, exc)
    return None


async def send_ticket_system_notification(
    settings: Settings,
    *,
    recipient_user_id: int,
    title: str,
    description: str,
    notification_type: str,
) -> None:
    secret = (settings.ws_internal_secret or "").strip()
    service = (settings.notifications_service_url or "").strip().rstrip("/")
    if not service:
        _log.error(
            "ticket notify skipped: NOTIFICATIONS_SERVICE_URL empty recipient=%s type=%s",
            recipient_user_id,
            notification_type,
        )
        return
    if not secret:
        _log.error(
            "ticket notify skipped: WS_INTERNAL_SECRET empty recipient=%s type=%s",
            recipient_user_id,
            notification_type,
        )
        return
    url = f"{service}/notifications/system"
    body = {
        "recipient_user_id": int(recipient_user_id),
        "title": title[:500],
        "description": description[:2000],
        "notification_type": (notification_type or "ticket")[:64],
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, json=body, headers={"X-Internal-Key": secret})
        if response.status_code >= 400:
            _log.warning(
                "ticket notify failed recipient=%s status=%s body=%s",
                recipient_user_id,
                response.status_code,
                (response.text or "")[:400],
            )
    except Exception:
        _log.exception("ticket notify error recipient=%s type=%s", recipient_user_id, notification_type)
