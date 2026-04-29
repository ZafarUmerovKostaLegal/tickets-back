

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from infrastructure.config import get_settings
from infrastructure.meeting_links import (
    body_preview_suggests_external_meeting,
    build_meeting_link_objects,
    classify_meeting_url,
    event_body_is_empty_for_fetch,
    event_meeting_urls_from_body_object,
    extract_urls_from_location,
)

_log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TPL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


_lock = asyncio.Lock()
_cached_token: str | None = None
_cached_expires: datetime | None = None
_credential_fingerprint: str | None = None

_SKEW = timedelta(minutes=2)


_EVENT_SELECT = (
    "id,subject,body,bodyPreview,webLink,start,end,location,organizer,"
    "isOnlineMeeting,onlineMeetingProvider,onlineMeeting,showAs,isCancelled,"
    "createdDateTime,lastModifiedDateTime"
)


def _user_segment(mailbox: str) -> str:
    return quote(mailbox.strip(), safe="")


def _cred_fp(tenant: str, client_id: str, client_secret: str) -> str:
    return f"{tenant}:{client_id}:{hash(client_secret) & 0xFFFF_FFFF_FFFF_FFFF}"


def invalidate_graph_token_cache() -> None:
    global _cached_token, _cached_expires, _credential_fingerprint
    _cached_token = None
    _cached_expires = None
    _credential_fingerprint = None


async def get_app_access_token() -> str:

    global _cached_token, _cached_expires, _credential_fingerprint
    s = get_settings()
    tenant_id, client_id, client_secret = s.graph_client_credentials()
    if not tenant_id or not client_id or not client_secret:
        raise ValueError(
            "Задайте MICROSOFT_TENANT_ID, MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET "
            "(или CALL_SCHEDULE_MICROSOFT_*) и в Azure: Application Calendars.Read + Calendars.ReadWrite, admin consent"
        )
    now = datetime.now(timezone.utc)
    fp = _cred_fp(tenant_id, client_id, client_secret)
    async with _lock:
        if fp != _credential_fingerprint:
            _cached_token = None
            _cached_expires = None
            _credential_fingerprint = fp

        if (
            _cached_token
            and _cached_expires
            and _cached_expires - _SKEW > now
        ):
            return _cached_token

        url = TOKEN_URL_TPL.format(tenant=tenant_id)
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
        r.raise_for_status()
        j = r.json()
        token = j["access_token"]
        exp_sec = int(j.get("expires_in", 3600))
        _cached_token = token
        _cached_expires = now + timedelta(seconds=exp_sec)
        _log.debug("Graph app token obtained, expires in %ss", exp_sec)
        return token


async def _graph_get_once(path_after_v1: str) -> Any:
    token = await get_app_access_token()
    url = f"{GRAPH}{path_after_v1}" if path_after_v1.startswith("/") else f"{GRAPH}/{path_after_v1}"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
    r.raise_for_status()
    if r.text:
        return r.json()
    return {}


async def _graph_get(path_after_v1: str) -> Any:
    try:
        return await _graph_get_once(path_after_v1)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            _log.warning(
                "Graph GET retry after %s: %s",
                e.response.status_code,
                (e.response.text or "")[:500],
            )
            invalidate_graph_token_cache()
            return await _graph_get_once(path_after_v1)
        raise


def _graph_post_headers(
    token: str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


async def _graph_post_once(
    path_after_v1: str,
    payload: dict,
    *,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    token = await get_app_access_token()
    url = f"{GRAPH}{path_after_v1}" if path_after_v1.startswith("/") else f"{GRAPH}/{path_after_v1}"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url,
            json=payload,
            headers=_graph_post_headers(token, extra_headers),
            timeout=60.0,
        )
    r.raise_for_status()
    if r.text:
        return r.json()
    return {}


async def _graph_post(
    path_after_v1: str,
    payload: dict,
    *,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    try:
        return await _graph_post_once(path_after_v1, payload, extra_headers=extra_headers)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            _log.warning(
                "Graph POST retry after %s: %s",
                e.response.status_code,
                (e.response.text or "")[:500],
            )
            invalidate_graph_token_cache()
            return await _graph_post_once(path_after_v1, payload, extra_headers=extra_headers)
        raise


def _enrich_event_with_join_url(
    ev: dict[str, Any],
    *,
    fallback_join_url: str | None = None,
) -> dict[str, Any]:

    out = dict(ev)
    all_urls: list[str] = []
    seen: set[str] = set()

    def _add(u: str) -> None:
        u = (u or "").strip()
        if u and u not in seen:
            seen.add(u)
            all_urls.append(u)

    om = out.get("onlineMeeting")
    if isinstance(om, dict):
        _add(om.get("joinUrl") or "")

    for u in event_meeting_urls_from_body_object(out):
        _add(u)

    for u in extract_urls_from_location(out.get("location")):
        _add(u)

    _add(out.get("webLink") or "")

    if fallback_join_url and fallback_join_url.strip():
        _add(fallback_join_url.strip())

    join: str | None = None
    if isinstance(om, dict):
        j = (om.get("joinUrl") or "").strip()
        if j:
            join = j
    if not join and fallback_join_url and fallback_join_url.strip():
        join = fallback_join_url.strip()
    if not join:
        for u in all_urls:
            if classify_meeting_url(u) != "other":
                join = u
                break
    if not join and all_urls:
        join = all_urls[0]
    if not join:
        wl = (out.get("webLink") or "").strip()
        if wl:
            join = wl

    s = get_settings()
    if s.call_schedule_prefer_zoom_join_over_teams:
        zfirst = next(
            (u for u in all_urls if classify_meeting_url(u) == "zoom"),
            None,
        )
        if zfirst:
            join = zfirst

    if join:
        out["meetingJoinUrl"] = join

    objs = build_meeting_link_objects([u for u in all_urls if u])
    if objs:
        out["meetingLinks"] = objs
    return out


async def list_calendars_for_mailbox(mailbox: str) -> list[dict[str, Any]]:

    seg = _user_segment(mailbox)
    j = await _graph_get(f"/users/{seg}/calendars?$top=100")
    return j.get("value", []) if isinstance(j, dict) else []


def _calendar_view_query_string(start: datetime, end: datetime) -> str:
    return urlencode(
        {
            "startDateTime": _iso(start),
            "endDateTime": _iso(end),
            "$select": _EVENT_SELECT,
            "$top": "200",
        }
    )


async def _merge_full_event_body_if_needed(mailbox: str, ev: dict[str, Any]) -> dict[str, Any]:

    if not event_body_is_empty_for_fetch(ev):
        return ev
    if not body_preview_suggests_external_meeting(
        (ev.get("bodyPreview") or "")
    ) or not ev.get("id"):
        return ev
    seg = _user_segment(mailbox)
    eidq = quote(str(ev["id"]), safe="")
    try:
        full = await _graph_get(
            f"/users/{seg}/events/{eidq}?$select=body,location,bodyPreview,onlineMeeting,webLink"
        )
    except httpx.HTTPStatusError as e:
        _log.debug("call_schedule: full event %s: %s", eidq, e.response.status_code)
        return ev
    if not isinstance(full, dict):
        return ev
    if not event_body_is_empty_for_fetch(full):
        merged = {**ev, "body": full.get("body")}
        if full.get("location") is not None:
            merged["location"] = full.get("location")
        return merged
    return ev


async def list_calendar_view(
    mailbox: str,
    calendar_id: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:

    seg = _user_segment(mailbox)
    q = _calendar_view_query_string(start, end)
    if calendar_id in ("", "default"):
        j = await _graph_get(f"/users/{seg}/calendar/calendarView?{q}")
    else:
        cal = quote(calendar_id, safe="")
        j = await _graph_get(f"/users/{seg}/calendars/{cal}/calendarView?{q}")
    raw = j.get("value", []) if isinstance(j, dict) else []
    out: list[dict[str, Any] | Any] = []
    for x in raw:
        if not isinstance(x, dict):
            out.append(x)
            continue
        x2 = await _merge_full_event_body_if_needed(mailbox, x)
        out.append(_enrich_event_with_join_url(x2))
    return out


def _iso(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat().replace("+00:00", "Z")


def _format_invitation_text(meeting_url: str, extra_body: str | None) -> str:
    lead = f"Ссылка на встречу (Join):\n{meeting_url.strip()}\n"
    if extra_body and str(extra_body).strip():
        return f"{lead}\n{str(extra_body).strip()}"
    return lead


async def create_calendar_event(
    mailbox: str,
    *,
    subject: str,
    start: datetime,
    end: datetime,
    body: str | None = None,
    meeting_url: str | None = None,
    calendar_id: str | None = None,
    time_zone: str = "UTC",
) -> dict[str, Any]:

    seg = _user_segment(mailbox)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    payload: dict[str, Any] = {
        "subject": subject,
        "start": {
            "dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": time_zone,
        },
        "end": {
            "dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": time_zone,
        },
    }
    murl = (meeting_url or "").strip() if meeting_url else ""
    if murl:
        if not murl.lower().startswith("https://"):
            raise ValueError("meetingUrl должен начинаться с https://")
        payload["body"] = {"contentType": "text", "content": _format_invitation_text(murl, body)}
    elif body and str(body).strip():
        payload["body"] = {"contentType": "text", "content": str(body).strip()}

    s = get_settings()
    if s.call_schedule_create_as_teams_meeting:
        payload["isOnlineMeeting"] = True
        prov = (s.call_schedule_online_meeting_provider or "teamsForBusiness").strip()
        if prov:
            payload["onlineMeetingProvider"] = prov


    prefer_tz = f'outlook.timezone="{time_zone}"'
    extra_headers = {"Prefer": prefer_tz}

    if not calendar_id or calendar_id in ("default",):
        ev = await _graph_post(
            f"/users/{seg}/calendar/events",
            payload,
            extra_headers=extra_headers,
        )
    else:
        cal = quote(calendar_id, safe="")
        ev = await _graph_post(
            f"/users/{seg}/calendars/{cal}/events",
            payload,
            extra_headers=extra_headers,
        )
    if isinstance(ev, dict):
        return _enrich_event_with_join_url(ev, fallback_join_url=murl or None)
    return ev
