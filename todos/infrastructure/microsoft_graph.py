

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from infrastructure.config import get_settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTHORIZE_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPES = ["Calendars.ReadWrite", "Mail.ReadWrite", "User.Read", "offline_access"]


def get_authorize_url(state: str, *, force_consent: bool = False) -> str:

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
        "https://graph.microsoft.com/Mail.ReadWrite",
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
    if force_consent:
        # Re-consent so Mail.ReadWrite is granted after scope expansion.
        params["prompt"] = "consent"
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


def _calendar_events_base_path(calendar_id: str | None) -> str:
    cid = (calendar_id or "").strip()
    if cid and cid != "default":
        return f"{GRAPH_BASE}/me/calendars/{cid}"
    return f"{GRAPH_BASE}/me/calendar"


async def list_calendars(access_token: str) -> list[dict[str, Any]]:
    url = f"{GRAPH_BASE}/me/calendars"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    r.raise_for_status()
    data = r.json()
    return data.get("value", [])


async def list_calendar_events(
    access_token: str,
    start: datetime | None = None,
    end: datetime | None = None,
    calendar_id: str | None = None,
) -> list[dict[str, Any]]:

    base = _calendar_events_base_path(calendar_id)
    if start and end:
        params = {
            "startDateTime": start.isoformat(),
            "endDateTime": end.isoformat(),
        }
        query = urlencode(params)
        url = f"{base}/calendarView?{query}"
    else:
        url = f"{GRAPH_BASE}/me/events" if not calendar_id or calendar_id == "default" else f"{base}/events"
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


async def probe_mail_read_write(access_token: str) -> bool:
    """True if the token can access mail (Mail.ReadWrite)."""
    token = (access_token or "").strip()
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{GRAPH_BASE}/me/mailFolders/drafts",
                headers={"Authorization": f"Bearer {token}"},
                params={"$top": "1", "$select": "id"},
            )
        return r.status_code == 200
    except Exception:
        return False


async def create_mail_draft(
    access_token: str,
    *,
    to_email: str,
    subject: str,
    body_html: str | None = None,
    body_text: str | None = None,
    to_name: str | None = None,
    pdf_base64: str | None = None,
    pdf_file_name: str | None = None,
) -> dict[str, Any]:
    """Create an Outlook draft via Graph (does not send). Returns message with webLink when available."""
    to = (to_email or "").strip()
    if not to or "@" not in to:
        raise ValueError("toEmail is required")
    subj = (subject or "").strip() or "(no subject)"
    html = (body_html or "").strip()
    text = (body_text or "").strip()
    if html:
        body = {"contentType": "HTML", "content": html}
    else:
        body = {"contentType": "Text", "content": text or ""}

    recipient: dict[str, Any] = {"emailAddress": {"address": to}}
    name = (to_name or "").strip()
    if name:
        recipient["emailAddress"]["name"] = name

    payload: dict[str, Any] = {
        "subject": subj,
        "body": body,
        "toRecipients": [recipient],
    }

    pdf_b64 = (pdf_base64 or "").strip()
    if pdf_b64:
        # Strip data-URL prefix if the client sent one.
        if "," in pdf_b64 and pdf_b64.lower().startswith("data:"):
            pdf_b64 = pdf_b64.split(",", 1)[1]
        fname = (pdf_file_name or "invoice.pdf").strip() or "invoice.pdf"
        if not fname.lower().endswith(".pdf"):
            fname = f"{fname}.pdf"
        # Simple attachments are limited (~3MB). Reject oversized payloads early.
        approx_bytes = int(len(pdf_b64) * 3 / 4)
        if approx_bytes > 3_000_000:
            raise ValueError(
                "PDF attachment is too large for Outlook draft upload (max ~3 MB). "
                "Reduce the invoice PDF size or send without embedding."
            )
        payload["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": fname,
                "contentType": "application/pdf",
                "contentBytes": pdf_b64,
            }
        ]

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{GRAPH_BASE}/me/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
    if r.status_code in (401, 403):
        detail = ""
        try:
            detail = str((r.json() or {}).get("error", {}).get("message") or "")
        except Exception:
            detail = (r.text or "")[:500]
        raise PermissionError(
            detail
            or "Outlook mail permission missing. Reconnect Outlook calendar to grant Mail.ReadWrite."
        )
    r.raise_for_status()
    return r.json()


def _odata_escape(value: str) -> str:
    return (value or "").replace("'", "''")


async def get_mail_draft_delivery_state(
    access_token: str,
    *,
    message_id: str,
    subject: str | None = None,
    created_after_iso: str | None = None,
) -> dict[str, Any]:
    """
    Track whether an Outlook draft was sent or discarded.

    Returns ``state``: ``pending`` | ``sent`` | ``missing``.
    ``missing`` means the draft id is gone; caller should keep polling briefly
    then treat prolonged missing (no Sent Items hit) as discarded.
    """
    token = (access_token or "").strip()
    mid = (message_id or "").strip()
    if not token or not mid:
        raise ValueError("messageId is required")

    headers = {"Authorization": f"Bearer {token}"}
    mid_q = quote(mid, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{GRAPH_BASE}/me/messages/{mid_q}",
            headers=headers,
            params={"$select": "id,isDraft,sentDateTime,subject"},
        )
        if r.status_code == 200:
            data = r.json() if r.content else {}
            is_draft = bool(data.get("isDraft", True))
            sent_at = data.get("sentDateTime")
            if (not is_draft) or sent_at:
                return {"state": "sent", "isDraft": False, "sentDateTime": sent_at}
            return {"state": "pending", "isDraft": True, "sentDateTime": None}

        if r.status_code not in (404, 410):
            detail = ""
            try:
                detail = str((r.json() or {}).get("error", {}).get("message") or "")
            except Exception:
                detail = (r.text or "")[:500]
            if r.status_code in (401, 403):
                raise PermissionError(
                    detail
                    or "Outlook mail permission missing. Reconnect Outlook calendar to grant Mail.ReadWrite."
                )
            r.raise_for_status()

        # Draft id gone: either sent (new id in Sent Items) or discarded.
        subj = (subject or "").strip()
        if not subj:
            return {"state": "missing", "isDraft": None, "sentDateTime": None}

        filt = f"subject eq '{_odata_escape(subj)}'"
        created_after = (created_after_iso or "").strip()
        if created_after:
            filt = f"{filt} and sentDateTime ge {created_after}"

        sr = await client.get(
            f"{GRAPH_BASE}/me/mailFolders/sentitems/messages",
            headers=headers,
            params={
                "$top": "5",
                "$select": "id,sentDateTime,subject",
                "$orderby": "sentDateTime desc",
                "$filter": filt,
            },
        )
        if sr.status_code in (401, 403):
            detail = ""
            try:
                detail = str((sr.json() or {}).get("error", {}).get("message") or "")
            except Exception:
                detail = (sr.text or "")[:500]
            raise PermissionError(
                detail
                or "Outlook mail permission missing. Reconnect Outlook calendar to grant Mail.ReadWrite."
            )
        if sr.status_code >= 400:
            # Draft gone but Sent Items query failed — keep waiting, do not discard yet.
            return {"state": "missing", "isDraft": None, "sentDateTime": None}

        items = (sr.json() or {}).get("value") or []
        if isinstance(items, list) and len(items) > 0:
            first = items[0] if isinstance(items[0], dict) else {}
            return {
                "state": "sent",
                "isDraft": False,
                "sentDateTime": first.get("sentDateTime"),
            }
        # Draft deleted/moved; Sent Items not visible yet (or user discarded).
        return {"state": "missing", "isDraft": None, "sentDateTime": None}
