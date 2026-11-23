"""Meeting → matter folder links (Kosta File in the Teams meeting side panel and in the Zoom App)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure import graph_meetings, zoom_api
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.graph_mail import GraphError
from infrastructure.models import MeetingLinkModel
from presentation.common import iso, now
from presentation.deps import Viewer, get_viewer
from presentation.routes.mail_rules import _target

router = APIRouter(tags=["file-manager"])

_checks: dict[str, asyncio.Task] = {}


ZOOM_PREFIX = "zoom:"


class LinkIn(BaseModel):
    chatId: str = Field(min_length=3, max_length=300)
    matterId: str = Field(min_length=1, max_length=80)
    folderId: str | None = Field(default=None, max_length=36)
    includeTranscript: bool = True
    autoRecord: bool = False


def _ready() -> bool:
    settings = get_settings()
    return settings.meeting_recordings_enabled and settings.graph_ready()


def _can_edit(link: MeetingLinkModel, viewer: Viewer) -> bool:
    return link.user_id == viewer.user_id or viewer.manager


def _link_out(link: MeetingLinkModel | None, viewer: Viewer) -> dict | None:
    if link is None:
        return None
    return {
        "id": link.id,
        "chatId": link.chat_id,
        "topic": link.topic,
        "startsAt": iso(link.starts_at),
        "matterId": link.matter_id,
        "matterName": link.matter_name,
        "matterCode": link.matter_code,
        "clientId": link.client_id,
        "clientName": link.client_name,
        "folderId": link.folder_id,
        "folderPath": link.folder_path,
        "includeTranscript": link.include_transcript,
        "autoRecord": link.auto_record,
        "enabled": link.enabled,
        "filedTotal": link.filed_total,
        "lastRunAt": iso(link.last_run_at),
        "lastFiledAt": iso(link.last_filed_at),
        "lastError": link.last_error or None,
        "ownerName": link.user_name,
        "mine": link.user_id == viewer.user_id,
        "canEdit": _can_edit(link, viewer),
    }


def _graph_failed(exc: GraphError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


async def _require_member(chat_id: str, viewer: Viewer) -> None:
    """Only people in the meeting may send its recording somewhere."""
    email = (viewer.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="У учётной записи нет почты Microsoft 365")
    try:
        members = await graph_meetings.chat_members(chat_id)
    except GraphError as exc:
        raise _graph_failed(exc) from exc
    if email not in members:
        raise HTTPException(status_code=403, detail="Вы не участник этой встречи")


async def _link(session: AsyncSession, chat_id: str) -> MeetingLinkModel | None:
    return await session.scalar(select(MeetingLinkModel).where(MeetingLinkModel.chat_id == chat_id).limit(1))


@router.get("/meetings/link")
async def get_link(
    chatId: str = Query(min_length=3, max_length=300),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    if chatId.startswith(ZOOM_PREFIX):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    link = await _link(session, chatId)
    if link is not None and not _can_edit(link, viewer):
        await _require_member(chatId, viewer)
    return {"link": _link_out(link, viewer), "serverReady": _ready()}


@router.put("/meetings/link")
async def put_link(body: LinkIn, session: AsyncSession = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    if not _ready():
        raise HTTPException(status_code=503, detail="Сохранение записей Teams не настроено на сервере")
    if body.chatId.startswith(ZOOM_PREFIX):
        raise HTTPException(status_code=400, detail="Это не чат встречи Teams")
    link = await _link(session, body.chatId)
    if link is not None and not _can_edit(link, viewer):
        raise HTTPException(status_code=403, detail=f"Встречу уже привязал(а) {link.user_name} — изменить может он(а) или партнёр")
    await _require_member(body.chatId, viewer)
    target = await _target(session, viewer, body.matterId, body.folderId)
    try:
        meeting = await graph_meetings.meeting_for_chat(body.chatId)
    except GraphError as exc:
        raise _graph_failed(exc) from exc
    warning = None
    if body.autoRecord:
        try:
            await graph_meetings.set_record_automatically(meeting.organizer_id, meeting.meeting_id)
        except GraphError as exc:
            warning = f"Автозапись не включилась ({exc}). Нажмите «Начать запись» в Teams вручную."
    stamp = now()
    if link is None:
        link = MeetingLinkModel(id=str(uuid4()), chat_id=body.chatId, user_id=viewer.user_id, created_at=stamp, filed_ids=[])
        session.add(link)
    if not link.user_name or link.user_id == viewer.user_id:
        link.user_id = viewer.user_id
        link.user_name = viewer.name[:200]
        link.user_email = (viewer.email or "").strip().lower()[:320]
    for key, value in target.items():
        setattr(link, key, value)
    link.topic = meeting.topic[:500]
    link.organizer_id = meeting.organizer_id
    link.meeting_id = meeting.meeting_id
    link.starts_at = meeting.starts_at
    link.include_transcript = body.includeTranscript
    link.auto_record = body.autoRecord and warning is None
    link.enabled = True
    link.last_error = ""
    link.updated_at = stamp
    await session.commit()
    return {"link": _link_out(link, viewer), "serverReady": True, "warning": warning}


@router.delete("/meetings/link")
async def delete_link(
    chatId: str = Query(min_length=3, max_length=300),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    link = await _link(session, chatId)
    if link is None:
        return {"ok": True}
    if not _can_edit(link, viewer):
        raise HTTPException(status_code=403, detail="Отвязать встречу может тот, кто её привязал, или партнёр")
    await session.delete(link)
    await session.commit()
    return {"ok": True}


@router.post("/meetings/link/check")
async def check_now(
    chatId: str = Query(min_length=3, max_length=300),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Look for recordings of this meeting right away instead of waiting for the next pass."""
    link = await _link(session, chatId)
    if link is None:
        raise HTTPException(status_code=404, detail="Встреча не привязана")
    if not _ready():
        raise HTTPException(status_code=503, detail="Сохранение записей Teams не настроено на сервере")
    _schedule_check(chatId)
    return {"started": True}


def _schedule_check(key: str) -> None:
    for done in [k for k, task in _checks.items() if task.done()]:
        _checks.pop(done, None)
    running = _checks.get(key)
    if running is None or running.done():
        from presentation.meetings import run_once

        _checks[key] = asyncio.create_task(run_once(chat_id=key))


class ZoomLinkIn(BaseModel):
    uuid: str = Field(min_length=8, max_length=200)
    meetingId: str = Field(min_length=6, max_length=20, pattern=r"^[0-9]+$")
    matterId: str = Field(min_length=1, max_length=80)
    folderId: str | None = Field(default=None, max_length=36)
    includeTranscript: bool = True


def _zoom_ready() -> bool:
    return get_settings().meeting_recordings_enabled and zoom_api.zoom_ready()


def _zoom_out(link: MeetingLinkModel | None, viewer: Viewer) -> dict | None:
    out = _link_out(link, viewer)
    if out is None or out["canEdit"]:
        return out
    # Someone else's link: only that the meeting is taken, not where its recording goes.
    return {"id": out["id"], "topic": out["topic"], "ownerName": out["ownerName"], "mine": False, "canEdit": False, "hidden": True}


@router.get("/meetings/zoom/link")
async def get_zoom_link(
    uuid: str = Query(min_length=8, max_length=200),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    link = await _link(session, ZOOM_PREFIX + uuid)
    return {"link": _zoom_out(link, viewer), "serverReady": _zoom_ready()}


@router.put("/meetings/zoom/link")
async def put_zoom_link(body: ZoomLinkIn, session: AsyncSession = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    """Participation is checked when the recording is filed (Zoom lists participants only after the meeting)."""
    if not _zoom_ready():
        raise HTTPException(status_code=503, detail="Сохранение записей Zoom не настроено на сервере")
    email = (viewer.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="У учётной записи нет почты")
    key = ZOOM_PREFIX + body.uuid
    link = await _link(session, key)
    if link is not None and not _can_edit(link, viewer):
        raise HTTPException(status_code=403, detail=f"Встречу уже привязал(а) {link.user_name} — изменить может он(а) или партнёр")
    target = await _target(session, viewer, body.matterId, body.folderId)
    try:
        meeting = await zoom_api.meeting(body.meetingId)
    except zoom_api.ZoomError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    stamp = now()
    if link is None:
        link = MeetingLinkModel(id=str(uuid4()), provider="zoom", chat_id=key, user_id=viewer.user_id, created_at=stamp, filed_ids=[])
        session.add(link)
    if not link.user_name or link.user_id == viewer.user_id:
        link.user_id = viewer.user_id
        link.user_name = viewer.name[:200]
        link.user_email = email[:320]
    for name, value in target.items():
        setattr(link, name, value)
    link.provider = "zoom"
    link.topic = meeting.topic[:500]
    link.meeting_id = meeting.id
    link.starts_at = meeting.starts_at
    link.include_transcript = body.includeTranscript
    link.auto_record = False
    link.enabled = True
    link.last_error = ""
    link.updated_at = stamp
    await session.commit()
    return {"link": _zoom_out(link, viewer), "serverReady": True, "warning": None}


@router.delete("/meetings/zoom/link")
async def delete_zoom_link(
    uuid: str = Query(min_length=8, max_length=200),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    link = await _link(session, ZOOM_PREFIX + uuid)
    if link is None:
        return {"ok": True}
    if not _can_edit(link, viewer):
        raise HTTPException(status_code=403, detail="Отвязать встречу может тот, кто её привязал, или партнёр")
    await session.delete(link)
    await session.commit()
    return {"ok": True}


@router.post("/meetings/zoom/link/check")
async def check_zoom_now(
    uuid: str = Query(min_length=8, max_length=200),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    link = await _link(session, ZOOM_PREFIX + uuid)
    if link is None or not _can_edit(link, viewer):
        raise HTTPException(status_code=404, detail="Встреча не привязана")
    if not _zoom_ready():
        raise HTTPException(status_code=503, detail="Сохранение записей Zoom не настроено на сервере")
    _schedule_check(link.chat_id)
    return {"started": True}
