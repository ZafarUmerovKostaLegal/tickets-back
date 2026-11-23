"""Zoom API for cloud recordings (Server-to-Server OAuth app of the firm's Zoom account).

Scopes: meeting:read:meeting:admin, cloud_recording:read:list_recording_files:admin,
meeting:read:list_past_participants:admin (or the classic meeting:read:admin + recording:read:admin).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from infrastructure.config import get_settings

API = "https://api.zoom.us/v2"
TOKEN_URL = "https://zoom.us/oauth/token"

_token: dict[str, Any] = {"value": "", "expires": datetime.min.replace(tzinfo=timezone.utc)}
_token_lock = asyncio.Lock()


class ZoomError(Exception):
    """Zoom refused or failed; the message is shown to the person who linked the meeting."""


class TooLarge(Exception):
    pass


@dataclass
class ZoomFile:
    id: str
    file_type: str
    recording_type: str
    started_at: datetime | None
    download_url: str
    status: str


@dataclass
class ZoomRecording:
    topic: str
    host_email: str
    files: list[ZoomFile] = field(default_factory=list)


@dataclass
class ZoomMeeting:
    id: str
    topic: str
    starts_at: datetime | None


def zoom_ready() -> bool:
    s = get_settings()
    return bool(s.zoom_account_id.strip() and s.zoom_client_id.strip() and s.zoom_client_secret.strip())


def reset_token_cache() -> None:
    _token["value"] = ""
    _token["expires"] = datetime.min.replace(tzinfo=timezone.utc)


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60.0, transport=transport)


async def app_token(transport: httpx.AsyncBaseTransport | None = None) -> str:
    s = get_settings()
    if not zoom_ready():
        raise ZoomError("Не заданы ZOOM_ACCOUNT_ID / ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET")
    async with _token_lock:
        now = datetime.now(timezone.utc)
        if _token["value"] and _token["expires"] - timedelta(minutes=5) > now:
            return str(_token["value"])
        async with _client(transport) as client:
            response = await client.post(
                TOKEN_URL,
                params={"grant_type": "account_credentials", "account_id": s.zoom_account_id.strip()},
                auth=(s.zoom_client_id.strip(), s.zoom_client_secret.strip()),
            )
        if response.status_code >= 400:
            raise ZoomError(f"Zoom не выдал токен ({response.status_code})")
        body = response.json()
        _token["value"] = body["access_token"]
        _token["expires"] = now + timedelta(seconds=int(body.get("expires_in", 3600)))
        return str(_token["value"])


def meeting_uuid_path(uuid: str) -> str:
    """Zoom wants UUIDs that start with "/" or contain "//" encoded twice."""
    once = quote(uuid, safe="")
    return quote(once, safe="") if uuid.startswith("/") or "//" in uuid else once


def _when(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _explain(response: httpx.Response, what: str) -> str:
    if response.status_code in (401, 403):
        return f"Zoom не дал доступ к {what}: проверьте права приложения Kosta в Zoom Marketplace"
    try:
        detail = response.json().get("message", "")
    except ValueError:
        detail = ""
    return f"Zoom {response.status_code}: {detail}"[:300]


async def _get(path: str, what: str, transport) -> httpx.Response:
    token = await app_token(transport)
    async with _client(transport) as client:
        return await client.get(f"{API}{path}", headers={"Authorization": f"Bearer {token}"})


async def meeting(meeting_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> ZoomMeeting:
    response = await _get(f"/meetings/{quote(meeting_id, safe='')}", "встрече", transport)
    if response.status_code == 404:
        raise ZoomError("Zoom не нашёл эту встречу в аккаунте фирмы — встречи внешних организаторов не поддерживаются")
    if response.status_code >= 400:
        raise ZoomError(_explain(response, "встрече"))
    body = response.json()
    return ZoomMeeting(str(body.get("id") or meeting_id), str(body.get("topic") or "").strip(), _when(body.get("start_time")))


async def recordings(uuid: str, *, transport: httpx.AsyncBaseTransport | None = None) -> ZoomRecording | None:
    """Cloud recording of one meeting occurrence; None while Zoom has nothing yet."""
    response = await _get(f"/meetings/{meeting_uuid_path(uuid)}/recordings", "записям встречи", transport)
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise ZoomError(_explain(response, "записям встречи"))
    body = response.json()
    files = [
        ZoomFile(
            id=str(row.get("id") or ""),
            file_type=str(row.get("file_type") or "").upper(),
            recording_type=str(row.get("recording_type") or "").lower(),
            started_at=_when(row.get("recording_start")),
            download_url=str(row.get("download_url") or ""),
            status=str(row.get("status") or "").lower(),
        )
        for row in body.get("recording_files") or []
    ]
    return ZoomRecording(str(body.get("topic") or "").strip(), str(body.get("host_email") or "").strip().lower(), files)


async def participant_emails(uuid: str, *, transport: httpx.AsyncBaseTransport | None = None) -> set[str]:
    emails: set[str] = set()
    next_token = ""
    for _ in range(20):
        query = f"?page_size=300{'&next_page_token=' + quote(next_token, safe='') if next_token else ''}"
        response = await _get(f"/past_meetings/{meeting_uuid_path(uuid)}/participants{query}", "участникам встречи", transport)
        if response.status_code >= 400:
            raise ZoomError(_explain(response, "участникам встречи"))
        body = response.json()
        for row in body.get("participants") or []:
            email = str(row.get("user_email") or "").strip().lower()
            if email:
                emails.add(email)
        next_token = str(body.get("next_page_token") or "")
        if not next_token:
            break
    return emails


def _zoom_download(url: str) -> bool:
    host = httpx.URL(url).host
    return url.startswith("https://") and (host == "zoom.us" or host.endswith(".zoom.us"))


async def download_to(url: str, target: Path, limit: int, *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    if not _zoom_download(url):
        raise ZoomError("Неожиданный адрес файла записи Zoom")
    token = await app_token(transport)
    size = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0), transport=transport, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status_code >= 400:
                await response.aread()
                raise ZoomError(_explain(response, "файлу записи"))
            with open(target, "wb") as fh:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise TooLarge()
                    fh.write(chunk)
    return size


async def download_text(url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    if not _zoom_download(url):
        raise ZoomError("Неожиданный адрес расшифровки Zoom")
    token = await app_token(transport)
    async with httpx.AsyncClient(timeout=60.0, transport=transport, follow_redirects=True) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        raise ZoomError(_explain(response, "расшифровке"))
    return response.text
