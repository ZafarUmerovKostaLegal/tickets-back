from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.security import safe_file_name
from application.storage import resolve_stored_file
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import FileGuardEventModel, FileGuardPolicyModel
from presentation.deps import get_current_user, journal_login_allowed

router = APIRouter(prefix="/api/v1/file-guard", tags=["file-guard"])


def require_journal_user(user: dict = Depends(get_current_user)) -> dict:
    if not journal_login_allowed(user):
        raise HTTPException(status_code=403, detail="Нет доступа к журналу файлов")
    return user


class PolicyBody(BaseModel):
    host: str = Field(default="*", max_length=120)
    windowsUser: str = Field(default="*", max_length=200)
    dailySendLimit: int | None = Field(default=None, ge=0, le=10000)


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
                "dailySendLimit": row.daily_send_limit,
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
    if row is None:
        row = FileGuardPolicyModel(
            id=str(uuid.uuid4()),
            host=host,
            windows_user=windows_user,
            daily_send_limit=body.dailySendLimit,
            updated_at=datetime.now(timezone.utc),
        )
        session.add(row)
    else:
        row.daily_send_limit = body.dailySendLimit
        row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {
        "id": row.id,
        "host": row.host,
        "windowsUser": row.windows_user,
        "dailySendLimit": row.daily_send_limit,
    }
