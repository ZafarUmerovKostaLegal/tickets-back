from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.security import safe_file_name
from application.storage import resolve_stored_file, unlink_stored_file
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import FileGuardEventModel, FileGuardPolicyModel
from presentation.deps import get_current_user, journal_login_allowed

_EVENT_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

router = APIRouter(prefix="/api/v1/file-guard", tags=["file-guard"])


def require_journal_user(user: dict = Depends(get_current_user)) -> dict:
    if not journal_login_allowed(user):
        raise HTTPException(status_code=403, detail="Нет доступа к журналу файлов")
    return user


class PolicyBody(BaseModel):
    host: str = Field(default="*", max_length=120)
    windowsUser: str = Field(default="*", max_length=200)
    dailyLimitMb: int | None = Field(default=None, ge=0, le=100000)


class StorageRemoveBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


def _event_out(row: FileGuardEventModel) -> dict:
    return {
        "id": row.id,
        "host": row.host,
        "windowsUser": row.windows_user,
        "occurredAt": row.occurred_at.isoformat(),
        "process": row.process_name,
        "pid": row.pid,
        "resource": row.resource,
        "filePath": row.file_path,
        "fileName": row.file_name,
        "kind": row.kind,
        "status": row.status,
        "channel": row.channel,
        "note": row.note,
        "fileSize": row.file_size,
        "hasFile": bool(row.stored_name),
    }


@router.get("/events")
async def list_events(
    host: str = Query(default="", max_length=120),
    windowsUser: str = Query(default="", max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
    _: dict = Depends(require_journal_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(FileGuardEventModel).order_by(FileGuardEventModel.occurred_at.desc()).limit(limit)
    if host.strip():
        stmt = stmt.where(FileGuardEventModel.host == host.strip())
    if windowsUser.strip():
        stmt = stmt.where(FileGuardEventModel.windows_user == windowsUser.strip())
    rows = list((await session.scalars(stmt)).all())
    return {"items": [_event_out(row) for row in rows]}


@router.get("/events/{event_id}/file")
async def download_event_file(
    event_id: str,
    _: dict = Depends(require_journal_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(FileGuardEventModel, event_id)
    if row is None or not row.stored_name:
        raise HTTPException(status_code=404, detail="Файл не сохранён")
    path = resolve_stored_file(get_settings().files_dir, row.stored_name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(
        path,
        filename=safe_file_name(row.file_name),
        media_type="application/octet-stream",
        content_disposition_type="attachment",
    )


@router.get("/policies")
async def list_policies(
    _: dict = Depends(require_journal_user),
    session: AsyncSession = Depends(get_session),
):
    rows = list((await session.scalars(select(FileGuardPolicyModel))).all())
    return {
        "items": [
            {
                "id": row.id,
                "host": row.host,
                "windowsUser": row.windows_user,
                "dailyLimitMb": row.daily_send_limit,
            }
            for row in rows
        ]
    }


@router.put("/policies")
async def upsert_policy(
    body: PolicyBody,
    _: dict = Depends(require_journal_user),
    session: AsyncSession = Depends(get_session),
):
    host = (body.host or "*").strip() or "*"
    windows_user = (body.windowsUser or "*").strip() or "*"
    row = await session.scalar(
        select(FileGuardPolicyModel).where(
            FileGuardPolicyModel.host == host,
            FileGuardPolicyModel.windows_user == windows_user,
        )
    )
    if body.dailyLimitMb is None:
        if row is not None:
            await session.delete(row)
            await session.commit()
        return {"id": "", "host": host, "windowsUser": windows_user, "dailyLimitMb": None, "removed": True}
    if row is None:
        row = FileGuardPolicyModel(
            id=str(uuid.uuid4()),
            host=host,
            windows_user=windows_user,
            daily_send_limit=body.dailyLimitMb,
            updated_at=datetime.now(timezone.utc),
        )
        session.add(row)
    else:
        row.daily_send_limit = body.dailyLimitMb
        row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {
        "id": row.id,
        "host": row.host,
        "windowsUser": row.windows_user,
        "dailyLimitMb": row.daily_send_limit,
        "removed": False,
    }


def _clean_event_ids(ids: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in ids:
        event_id = (raw or "").strip()
        if not _EVENT_ID.fullmatch(event_id):
            raise HTTPException(status_code=400, detail="Некорректный идентификатор файла")
        if event_id in seen:
            continue
        seen.add(event_id)
        cleaned.append(event_id)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Не выбраны файлы")
    return cleaned


def _catalog_item(group: list[FileGuardEventModel]) -> dict:
    ordered = sorted(group, key=lambda row: row.occurred_at)
    first = ordered[0]
    occurred = first.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    people: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in ordered:
        key = (row.host, row.windows_user)
        if key in seen:
            continue
        seen.add(key)
        people.append({"host": row.host, "windowsUser": row.windows_user})
    size = max((int(row.file_size or 0) for row in ordered), default=0)
    return {
        "id": first.id,
        "fileName": first.file_name or "file",
        "fileSize": size,
        "host": first.host,
        "windowsUser": first.windows_user,
        "occurredAt": occurred.isoformat(),
        "copies": len(ordered),
        "users": people,
    }


@router.get("/storage")
async def list_storage(
    _: dict = Depends(require_journal_user),
    session: AsyncSession = Depends(get_session),
):
    rows = list((await session.scalars(
        select(FileGuardEventModel)
        .where(FileGuardEventModel.stored_name.is_not(None))
        .order_by(FileGuardEventModel.occurred_at.desc())
        .limit(20000)
    )).all())
    groups: dict[str, list[FileGuardEventModel]] = {}
    for row in rows:
        if not row.stored_name:
            continue
        groups.setdefault(row.stored_name, []).append(row)
    ranked = []
    for group in groups.values():
        first = min(group, key=lambda row: row.occurred_at)
        ranked.append((first.occurred_at, _catalog_item(group)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    items = [item[1] for item in ranked]
    return {
        "totalBytes": sum(int(item["fileSize"]) for item in items),
        "items": items,
    }


async def _drop_stored_files(session: AsyncSession, event_ids: list[str]) -> dict:
    ids = _clean_event_ids(event_ids)
    rows = list((await session.scalars(
        select(FileGuardEventModel).where(FileGuardEventModel.id.in_(ids))
    )).all())
    names = sorted({row.stored_name for row in rows if row.stored_name})
    if not names:
        raise HTTPException(status_code=404, detail="Файл уже удалён")
    related = list((await session.scalars(
        select(FileGuardEventModel).where(FileGuardEventModel.stored_name.in_(names))
    )).all())
    files_dir = get_settings().files_dir
    freed = 0
    for name in names:
        freed += unlink_stored_file(files_dir, name)
    for row in related:
        row.stored_name = None
    await session.commit()
    return {"removedFiles": len(names), "freedBytes": freed, "clearedEvents": len(related)}


@router.delete("/storage/{event_id}")
async def delete_stored_file(
    event_id: str,
    _: dict = Depends(require_journal_user),
    session: AsyncSession = Depends(get_session),
):
    return await _drop_stored_files(session, [event_id])


@router.post("/storage/remove")
async def delete_stored_files(
    body: StorageRemoveBody,
    _: dict = Depends(require_journal_user),
    session: AsyncSession = Depends(get_session),
):
    return await _drop_stored_files(session, body.ids)
