"""Microsoft Graph for Teams meeting recordings (application permissions + admin consent).

Needed in Azure (application, admin consent): Chat.ReadBasic.All, ChatMember.Read.All,
OnlineMeetings.ReadWrite.All, OnlineMeetingRecording.Read.All, OnlineMeetingTranscript.Read.All.
Teams also needs an application access policy for the app (New-CsApplicationAccessPolicy /
Grant-CsApplicationAccessPolicy -Global), otherwise Graph answers 403 for meeting data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from infrastructure.graph_mail import GRAPH, GraphError, _client, app_token


@dataclass
class MeetingInfo:
    chat_id: str
    topic: str
    join_url: str
    organizer_id: str
    meeting_id: str = ""
    starts_at: datetime | None = None


@dataclass
class MeetingFile:
    id: str
    created_at: datetime | None
    content_url: str


class TooLarge(Exception):
    """The recording is bigger than the file manager accepts."""


def _explain(response: httpx.Response, what: str) -> str:
    if response.status_code == 403:
        return (f"Microsoft 365 не дал доступ к {what}: нужны разрешения Teams для приложения Kosta "
                "и политика доступа приложения в Teams (см. инструкцию администратора)")
    if response.status_code == 404:
        return f"Microsoft 365 не нашёл {what}"
    try:
        detail = response.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    return f"Microsoft Graph {response.status_code}: {detail}"[:300]


def _when(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _get(path_or_url: str, what: str, transport: httpx.AsyncBaseTransport | None) -> dict:
    token = await app_token(transport)
    url = path_or_url if path_or_url.startswith("https://") else f"{GRAPH}{path_or_url}"
    async with _client(transport) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        raise GraphError(_explain(response, what))
    return response.json()


async def chat_members(chat_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> set[str]:
    """Lower-case e-mails of the meeting chat members."""
    body = await _get(f"/chats/{quote(chat_id)}/members", "участникам встречи", transport)
    emails: set[str] = set()
    for row in body.get("value") or []:
        email = str(row.get("email") or "").strip().lower()
        if email:
            emails.add(email)
    return emails


async def meeting_for_chat(chat_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> MeetingInfo:
    """The online meeting behind a Teams meeting chat (organizer and meeting id come from Graph, not the client)."""
    chat = await _get(f"/chats/{quote(chat_id)}", "встрече Teams", transport)
    info = chat.get("onlineMeetingInfo") or {}
    join_url = str(info.get("joinWebUrl") or "").strip()
    organizer_id = str(((info.get("organizer") or {}).get("id")) or "").strip()
    if not join_url or not organizer_id:
        raise GraphError("Это не чат встречи Teams — откройте Kosta File во встрече")
    meeting = MeetingInfo(chat_id=chat_id, topic=str(chat.get("topic") or "").strip(), join_url=join_url, organizer_id=organizer_id)
    escaped = join_url.replace("'", "''")
    found = await _get(
        f"/users/{quote(organizer_id)}/onlineMeetings?$filter=JoinWebUrl%20eq%20'{quote(escaped, safe='')}'",
        "встрече Teams",
        transport,
    )
    rows = found.get("value") or []
    if not rows:
        raise GraphError("Microsoft 365 не нашёл встречу организатора — встречи внешних организаторов не поддерживаются")
    row = rows[0]
    meeting.meeting_id = str(row.get("id") or "")
    meeting.topic = meeting.topic or str(row.get("subject") or "").strip()
    meeting.starts_at = _when(row.get("startDateTime"))
    return meeting


async def set_record_automatically(organizer_id: str, meeting_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
    token = await app_token(transport)
    async with _client(transport) as client:
        response = await client.patch(
            f"{GRAPH}/users/{quote(organizer_id)}/onlineMeetings/{quote(meeting_id)}",
            headers={"Authorization": f"Bearer {token}"},
            json={"recordAutomatically": True},
        )
    if response.status_code >= 400:
        raise GraphError(_explain(response, "настройкам встречи"))


async def _list(organizer_id: str, meeting_id: str, kind: str, url_key: str, transport) -> list[MeetingFile]:
    body = await _get(
        f"/users/{quote(organizer_id)}/onlineMeetings/{quote(meeting_id)}/{kind}",
        "записям встречи" if kind == "recordings" else "расшифровкам встречи",
        transport,
    )
    rows = []
    for row in body.get("value") or []:
        file_id = str(row.get("id") or "")
        url = str(row.get(url_key) or "")
        if file_id and url.startswith(GRAPH):
            rows.append(MeetingFile(file_id, _when(row.get("createdDateTime")), url))
    return rows


async def list_recordings(organizer_id: str, meeting_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> list[MeetingFile]:
    return await _list(organizer_id, meeting_id, "recordings", "recordingContentUrl", transport)


async def list_transcripts(organizer_id: str, meeting_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> list[MeetingFile]:
    return await _list(organizer_id, meeting_id, "transcripts", "transcriptContentUrl", transport)


async def download_to(url: str, target: Path, limit: int, *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    """Streams a recording to disk (they are often hundreds of MB). Returns the size."""
    if not url.startswith(GRAPH):
        raise GraphError("Неожиданный адрес файла записи")
    token = await app_token(transport)
    size = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0), transport=transport, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status_code >= 400:
                await response.aread()
                raise GraphError(_explain(response, "файлу записи"))
            with open(target, "wb") as fh:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise TooLarge()
                    fh.write(chunk)
    return size


async def download_transcript(url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    if not url.startswith(GRAPH):
        raise GraphError("Неожиданный адрес расшифровки")
    token = await app_token(transport)
    separator = "&" if "?" in url else "?"
    async with _client(transport) as client:
        response = await client.get(f"{url}{separator}$format=text/vtt", headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        raise GraphError(_explain(response, "расшифровке"))
    return response.text
