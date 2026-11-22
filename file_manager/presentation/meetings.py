"""Teams meeting recordings → matter folders.

Someone in a Teams meeting opens Kosta File in the meeting side panel and links the meeting to a matter
folder. Every few minutes the server asks Graph for recordings (and transcripts) of linked meetings and
files each new one into that folder, in the name of the person who linked the meeting.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from application.meeting_files import recording_file_name, transcript_file_name, vtt_to_text
from infrastructure import blobs
from infrastructure.config import get_settings
from infrastructure.database import async_session_factory
from infrastructure.graph_mail import GraphError
from infrastructure.graph_meetings import (
    TooLarge,
    download_to,
    download_transcript,
    list_recordings,
    list_transcripts,
    meeting_for_chat,
)
from infrastructure.models import AclModel, FolderModel, MatterWorkspaceModel, MeetingLinkModel
from infrastructure.tt_directory import Matter
from presentation.common import next_doc_number, now
from presentation.deps import Viewer
from presentation.intake import file_into_matter
from presentation.versioning import digest, store_content

_log = logging.getLogger(__name__)
_run_lock = asyncio.Lock()
_task: asyncio.Task | None = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def watched(link: MeetingLinkModel, at: datetime) -> bool:
    last = max(_aware(link.created_at), _aware(link.last_filed_at or link.created_at), _aware(link.updated_at))
    return at - last < timedelta(days=max(1, get_settings().meeting_watch_days))


def _viewer(link: MeetingLinkModel) -> Viewer:
    return Viewer(link.user_id, link.user_name or link.user_email, False, False, "", email=link.user_email)


async def _folder_id(session: AsyncSession, link: MeetingLinkModel) -> str | None:
    if not link.folder_id:
        return None
    folder = await session.get(FolderModel, link.folder_id)
    if folder is None or folder.matter_id != link.matter_id or folder.deleted_at is not None:
        return None
    return folder.id


async def _file(
    session: AsyncSession,
    link: MeetingLinkModel,
    *,
    name: str,
    content_type: str,
    sha: str,
    size: int,
    head: bytes,
    comment: str,
) -> str:
    workspace = await session.get(MatterWorkspaceModel, link.matter_id)
    if workspace is None:
        return "no-matter"
    matter = Matter(link.matter_id, link.client_id or workspace.client_id, link.matter_name, link.matter_code, False)
    first = await next_doc_number(session)
    numbers = iter(range(first, first + 10))

    async def next_number() -> int:
        return next(numbers)

    status, _ = await file_into_matter(
        session, _viewer(link), matter,
        folder_id=await _folder_id(session, link),
        name=name,
        content_type=content_type,
        size=size,
        sha=sha,
        head=head,
        comment=comment,
        allow_duplicate=True,
        stamp=now(),
        next_number=next_number,
    )
    return status


def _mark(link: MeetingLinkModel, file_id: str, filed: bool) -> None:
    link.filed_ids = [*(link.filed_ids or []), file_id]
    if filed:
        link.filed_total = int(link.filed_total or 0) + 1
        link.last_filed_at = now()


async def _file_recordings(session: AsyncSession, link: MeetingLinkModel, transport) -> int:
    filed = 0
    limit = get_settings().max_upload_bytes
    for recording in await list_recordings(link.organizer_id, link.meeting_id, transport=transport):
        key = f"rec:{recording.id}"
        if key in (link.filed_ids or []):
            continue
        part = blobs.part_path(f"meeting-{uuid4()}")
        try:
            try:
                size = await download_to(recording.content_url, part, limit, transport=transport)
            except TooLarge:
                link.last_error = "Запись больше допустимого размера файла — сохраните её вручную"
                _mark(link, key, False)
                continue
            sha, size = await run_in_threadpool(blobs.adopt, part)
        finally:
            await run_in_threadpool(lambda: part.unlink(missing_ok=True))
        head = await run_in_threadpool(blobs.read_head, sha)
        status = await _file(
            session, link,
            name=recording_file_name(link.topic, recording.created_at or link.starts_at),
            content_type="video/mp4", sha=sha, size=size, head=head,
            comment="Запись встречи Teams",
        )
        _mark(link, key, status in ("created", "version"))
        filed += status in ("created", "version")
        await session.commit()
    return filed


async def _file_transcripts(session: AsyncSession, link: MeetingLinkModel, transport) -> int:
    filed = 0
    for transcript in await list_transcripts(link.organizer_id, link.meeting_id, transport=transport):
        key = f"tr:{transcript.id}"
        if key in (link.filed_ids or []):
            continue
        text = vtt_to_text(await download_transcript(transcript.content_url, transport=transport), link.topic)
        content = text.encode("utf-8")
        sha = digest(content)
        await store_content(content)
        status = await _file(
            session, link,
            name=transcript_file_name(link.topic, transcript.created_at or link.starts_at),
            content_type="text/plain; charset=utf-8", sha=sha, size=len(content), head=content[:65536],
            comment="Расшифровка встречи Teams",
        )
        _mark(link, key, status in ("created", "version"))
        filed += status in ("created", "version")
        await session.commit()
    return filed


async def process_link(session: AsyncSession, link: MeetingLinkModel, *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    """Files new recordings / transcripts of one linked meeting. Returns how many were filed."""
    acl = await session.get(AclModel, (link.matter_id, link.user_id))
    if acl is not None and acl.level == "deny":
        link.enabled = False
        link.last_error = "Нет доступа к matter — привязка выключена"
        return 0
    if not link.meeting_id or not link.organizer_id:
        meeting = await meeting_for_chat(link.chat_id, transport=transport)
        link.organizer_id, link.meeting_id = meeting.organizer_id, meeting.meeting_id
        link.topic = link.topic or meeting.topic[:500]
        link.starts_at = link.starts_at or meeting.starts_at
    link.last_error = ""
    filed = await _file_recordings(session, link, transport)
    if link.include_transcript:
        filed += await _file_transcripts(session, link, transport)
    return filed


async def run_once(*, chat_id: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """One pass over linked meetings (or only one of them); passes are never parallel."""
    settings = get_settings()
    if not settings.meeting_recordings_enabled:
        return {"skipped": "disabled"}
    if not settings.graph_ready() and transport is None:
        return {"skipped": "graph-not-configured"}
    async with _run_lock:
        summary = {"meetings": 0, "filed": 0, "errors": 0}
        async with async_session_factory() as session:
            query = select(MeetingLinkModel.id).where(MeetingLinkModel.enabled.is_(True))
            if chat_id:
                query = query.where(MeetingLinkModel.chat_id == chat_id)
            ids = list(await session.scalars(query))
            stamp = now()
            for link_id in ids:
                link = await session.get(MeetingLinkModel, link_id)
                if link is None or (not chat_id and not watched(link, stamp)):
                    continue
                summary["meetings"] += 1
                error = ""
                try:
                    summary["filed"] += await process_link(session, link, transport=transport)
                except GraphError as exc:
                    error = str(exc)
                except Exception as exc:  # noqa: BLE001 — one meeting must never stop the others
                    _log.exception("meeting recording filing failed for %s", link_id)
                    error = f"Ошибка сохранения записи: {exc}"
                if error:
                    await session.rollback()
                    summary["errors"] += 1
                link = await session.get(MeetingLinkModel, link_id)
                if link is None:
                    continue
                if error:
                    link.last_error = error[:500]
                link.last_run_at = now()
                await session.commit()
        return summary


async def _loop() -> None:
    interval = max(60, int(get_settings().meeting_recordings_interval_sec))
    await asyncio.sleep(45)
    while True:
        try:
            await run_once()
        except Exception:  # noqa: BLE001
            _log.exception("meeting recordings pass failed")
        await asyncio.sleep(interval)


def start_meetings_loop() -> None:
    global _task
    settings = get_settings()
    if not settings.meeting_recordings_enabled or not settings.graph_ready():
        _log.info("Teams recordings filing is off (FILE_MANAGER_MEETING_RECORDINGS or Graph credentials missing)")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="meeting-recordings")


async def stop_meetings_loop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
