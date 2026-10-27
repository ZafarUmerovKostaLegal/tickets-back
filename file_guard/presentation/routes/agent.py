from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application.notify import notify_file_attempt
from application.policy import pick_daily_send_limit
from application.security import safe_file_name
from application.storage import directory_size, resolve_stored_file
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import FileGuardEventModel, FileGuardPolicyModel
from presentation.deps import require_agent_token

router = APIRouter(prefix="/api/v1/file-guard/agent", tags=["file-guard-agent"])

_KINDS = {"send", "clip", "copy", "edit", "new", "open", "access"}
_STATUSES = {"blocked"}
_CHANNELS = {"telegram", "browser", "clip", "net", "fs", ""}


class AgentEventMeta(BaseModel):
    clientEventId: str = Field(min_length=8, max_length=80)
    host: str = Field(min_length=1, max_length=120)
    windowsUser: str = Field(min_length=1, max_length=200)
    occurredAt: datetime
    process: str = Field(default="", max_length=260)
    pid: int | None = Field(default=None, ge=0, le=2_147_483_647)
    resource: str = Field(default="", max_length=500)
    filePath: str = Field(default="", max_length=2000)
    fileName: str = Field(default="", max_length=260)
    kind: str = "send"
    status: str = "blocked"
    channel: str = ""
    note: str = Field(default="", max_length=500)


def _clip(value: str, limit: int) -> str:
    return (value or "").replace("\x00", "").strip()[:limit]


def _channel_from_process(process: str, resource: str) -> str:
    name = process.lower()
    if "telegram" in name:
        return "telegram"
    if name in {"chrome.exe", "msedge.exe", "firefox.exe", "browser.exe", "opera.exe"}:
        return "browser"
    if "буфер" in resource.lower() or "clipboard" in resource.lower():
        return "clip"
    return "fs"


async def _policy_limit(session: AsyncSession, host: str, windows_user: str) -> int | None:
    rows = list((await session.scalars(select(FileGuardPolicyModel))).all())
    return pick_daily_send_limit(rows, host=host, windows_user=windows_user)


async def _events_created_today(session: AsyncSession) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count = await session.scalar(
        select(func.count()).select_from(FileGuardEventModel).where(FileGuardEventModel.created_at >= start)
    )
    return int(count or 0)


async def _sends_today(session: AsyncSession, host: str, windows_user: str, moment: datetime) -> int:
    start = moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count = await session.scalar(
        select(func.count()).select_from(FileGuardEventModel).where(
            FileGuardEventModel.host == host,
            FileGuardEventModel.windows_user == windows_user,
            FileGuardEventModel.occurred_at >= start,
            FileGuardEventModel.kind == "send",
            FileGuardEventModel.status == "allowed",
        )
    )
    return int(count or 0)


async def _same_stored_file(session: AsyncSession, digest: str) -> FileGuardEventModel | None:
    return await session.scalar(
        select(FileGuardEventModel).where(
            FileGuardEventModel.file_sha256 == digest,
            FileGuardEventModel.stored_name.is_not(None),
        ).order_by(FileGuardEventModel.occurred_at.asc()).limit(1)
    )


async def _ack(session: AsyncSession, row: FileGuardEventModel, *, duplicate: bool) -> dict:
    limit = await _policy_limit(session, row.host, row.windows_user)
    used = await _sends_today(session, row.host, row.windows_user, row.occurred_at)
    return {
        "id": row.id,
        "duplicate": duplicate,
        "storedFile": bool(row.stored_name),
        "dailySendLimit": limit,
        "sendsToday": used,
        "blockAll": limit is None,
    }


@router.get("/policy")
async def agent_policy(
    host: str = Query(min_length=1, max_length=120),
    windowsUser: str = Query(min_length=1, max_length=200),
    _: None = Depends(require_agent_token),
    session: AsyncSession = Depends(get_session),
):
    host = _clip(host, 120)
    windows_user = _clip(windowsUser, 200)
    limit = await _policy_limit(session, host, windows_user)
    used = await _sends_today(session, host, windows_user, datetime.now(timezone.utc))
    return {
        "dailySendLimit": limit,
        "sendsToday": used,
        "blockAll": limit is None,
    }


@router.post("/events", status_code=201)
async def ingest_event(
    meta: str = Form(...),
    file: UploadFile | None = File(default=None),
    _: None = Depends(require_agent_token),
    session: AsyncSession = Depends(get_session),
):
    try:
        payload = AgentEventMeta.model_validate(json.loads(meta))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректное описание события") from exc
    if payload.kind not in _KINDS or payload.status not in _STATUSES or payload.channel not in _CHANNELS:
        raise HTTPException(status_code=400, detail="Некорректный тип события")

    existing = await session.scalar(
        select(FileGuardEventModel).where(FileGuardEventModel.client_event_id == payload.clientEventId)
    )
    if existing is not None:
        return await _ack(session, existing, duplicate=True)

    host = _clip(payload.host, 120)
    windows_user = _clip(payload.windowsUser, 200)
    if not host or not windows_user:
        raise HTTPException(status_code=400, detail="Некорректное описание события")
    settings = get_settings()

    file_name = safe_file_name(payload.fileName or payload.filePath)
    stored_name: str | None = None
    stored_path: Path | None = None
    digest: str | None = None
    size: int | None = None
    if file is not None and file.filename:
        body = await file.read(settings.max_upload_bytes + 1)
        if len(body) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Файл больше допустимого размера")
        if body:
            digest = hashlib.sha256(body).hexdigest()
            size = len(body)
            file_name = safe_file_name(file.filename or file_name)
            prior = await _same_stored_file(session, digest)
            if prior is not None and prior.stored_name:
                stored_name = prior.stored_name
            elif directory_size(settings.files_dir) + len(body) <= settings.max_stored_bytes:
                stored_name = uuid.uuid4().hex
                stored_path = resolve_stored_file(settings.files_dir, stored_name)
                if stored_path is None:
                    stored_name = None
                else:
                    stored_path.parent.mkdir(parents=True, exist_ok=True)
                    stored_path.write_bytes(body)

    if await _events_created_today(session) >= settings.max_events_per_day:
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=429, detail="Слишком много событий за сегодня")

    occurred = payload.occurredAt
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    channel = payload.channel or _channel_from_process(payload.process, payload.resource)
    limit = await _policy_limit(session, host, windows_user)
    used = await _sends_today(session, host, windows_user, occurred)
    stored_status = "blocked" if limit is None or used >= limit else "allowed"
    row = FileGuardEventModel(
        id=str(uuid.uuid4()),
        client_event_id=_clip(payload.clientEventId, 80),
        host=host,
        windows_user=windows_user,
        occurred_at=occurred,
        process_name=_clip(payload.process, 260),
        pid=payload.pid,
        resource=_clip(payload.resource, 500),
        file_path=_clip(payload.filePath, 2000),
        file_name=file_name,
        kind=payload.kind,
        status=stored_status,
        channel=channel,
        note=_clip(payload.note, 500),
        file_sha256=digest,
        file_size=size,
        stored_name=stored_name,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        existing = await session.scalar(
            select(FileGuardEventModel).where(FileGuardEventModel.client_event_id == payload.clientEventId)
        )
        if existing is None and digest:
            existing = await _same_stored_file(session, digest)
        if existing is None:
            raise
        return await _ack(session, existing, duplicate=True)
    except Exception:
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise
    try:
        await notify_file_attempt(
        windows_user=windows_user,
        host=host,
        file_name=file_name,
        resource=row.resource,
        )
    except Exception:
        pass
    limit = await _policy_limit(session, host, windows_user)
    used = await _sends_today(session, host, windows_user, occurred)
    return {
        "id": row.id,
        "duplicate": False,
        "storedFile": stored_name is not None,
        "dailySendLimit": limit,
        "sendsToday": used,
        "blockAll": limit is None,
    }
