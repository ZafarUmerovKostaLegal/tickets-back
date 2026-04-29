

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from infrastructure.config import get_settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTHORIZE_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPES = ["Calendars.ReadWrite", "User.Read", "offline_access"]


def get_authorize_url(state: str) -> str:

    s = get_settings()
    client_id = (s.microsoft_client_id or "").strip()
    if not client_id:
        raise ValueError(
            "MICROSOFT_CLIENT_ID is empty. Set it in the todos service environment (Azure App Registration → Application ID)."
        )
    redirect_uri = (s.microsoft_redirect_uri or "").strip()
    if not redirect_uri.startswith(("http://", "https://")) or " " in redirect_uri or "\n" in redirect_uri:
        raise ValueError(
            "MICROSOFT_REDIRECT_URI must be a single absolute URL (gateway callback), e.g. "
            "http://localhost:1234/api/v1/todos/calendar/callback — not the Vite dev server (5173). "
            "Must match Azure app «Redirect URIs» exactly."
        )
    scope_parts = [
        "https://graph.microsoft.com/Calendars.ReadWrite",
        "https://graph.microsoft.com/User.Read",
        "offline_access",
    ]
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(scope_parts),
        "response_mode": "query",
        "state": state,
    }
    base = AUTHORIZE_URL_TEMPLATE.format(tenant=(s.microsoft_tenant_id or "common").strip())
    return f"{base}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> dict[str, Any]:

    s = get_settings()
    url = TOKEN_URL_TEMPLATE.format(tenant=s.microsoft_tenant_id or "common")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            data={
                "client_id": s.microsoft_client_id,
                "client_secret": s.microsoft_client_secret,
                "code": code,
                "redirect_uri": s.microsoft_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    r.raise_for_status()
    data = r.json()
    expires_in = data.get("expires_in", 3600)
    from datetime import timedelta
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or "",
        "expires_at": expires_at,
    }


async def refresh_tokens(refresh_token: str) -> dict[str, Any]:

    s = get_settings()
    url = TOKEN_URL_TEMPLATE.format(tenant=s.microsoft_tenant_id or "common")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            data={
                "client_id": s.microsoft_client_id,
                "client_secret": s.microsoft_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    r.raise_for_status()
    data = r.json()
    expires_in = data.get("expires_in", 3600)
    from datetime import timedelta
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_at": expires_at,
    }


async def list_calendar_events(
    access_token: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:

    if start and end:
        params = {
            "startDateTime": start.isoformat(),
            "endDateTime": end.isoformat(),
        }
        query = urlencode(params)
        url = f"{GRAPH_BASE}/me/calendar/calendarView?{query}"
    else:
        url = f"{GRAPH_BASE}/me/events"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    r.raise_for_status()
    data = r.json()
    return data.get("value", [])


async def create_calendar_event(
    access_token: str,
    subject: str,
    start: datetime,
    end: datetime,
    body: str | None = None,
) -> dict[str, Any]:

    payload = {
        "subject": subject,
        "start": {
            "dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
    }
    if body is not None:
        payload["body"] = {"contentType": "text", "content": body}
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{GRAPH_BASE}/me/events",
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
    r.raise_for_status()
    return r.json()
