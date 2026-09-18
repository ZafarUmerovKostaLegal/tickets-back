from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import Request

from infrastructure.auth_upstream import access_token_from_request, auth_service_request
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
    request: Request,
    authorization: Optional[str],
    user_id: int,
) -> dict | None:
    """Load public user profile via auth service (Bearer header or session cookie)."""
    del settings  # reserved for callers; auth base comes from auth_service_request
    token = access_token_from_request(request, authorization)
    if not token:
        _log.warning("ticket partner lookup skipped: no access token user_id=%s", user_id)
        return None
    try:
        r = await auth_service_request(
            "GET",
            f"/users/{user_id}/public",
            f"Bearer {token}",
            timeout=8.0,
        )
    except Exception as exc:
        _log.warning("auth user public fetch failed user_id=%s err=%s", user_id, exc)
        return None
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            return data
    _log.warning(
        "auth user public fetch status=%s user_id=%s body=%s",
        r.status_code,
        user_id,
        (r.text or "")[:200],
    )
    return None


async def fetch_partner_from_partners_list(
    request: Request,
    authorization: Optional[str],
    user_id: int,
) -> dict | None:
    """Fallback: same source as UI listPartners (`GET /users/partners`)."""
    token = access_token_from_request(request, authorization)
    if not token:
        return None
    try:
        r = await auth_service_request(
            "GET",
            "/users/partners",
            f"Bearer {token}",
            timeout=10.0,
        )
    except Exception as exc:
        _log.warning("auth partners list fetch failed err=%s", exc)
        return None
    if r.status_code != 200:
        _log.warning("auth partners list status=%s body=%s", r.status_code, (r.text or "")[:200])
        return None
    payload = r.json()
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return None
    for raw in items:
        if not isinstance(raw, dict):
            continue
        try:
            if int(raw.get("id")) == int(user_id):
                return raw
        except (TypeError, ValueError):
            continue
    return None


async def resolve_ticket_approval_partner(
    settings: Settings,
    request: Request,
    authorization: Optional[str],
    partner_user_id: int,
) -> tuple[dict | None, bool]:
    """Return (profile, trusted_as_partner). trusted_as_partner when found in /users/partners."""
    listed = await fetch_partner_from_partners_list(request, authorization, partner_user_id)
    if listed:
        return listed, True
    profile = await fetch_auth_user_public(settings, request, authorization, partner_user_id)
    return profile, False


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
