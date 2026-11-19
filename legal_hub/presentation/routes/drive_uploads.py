"""Disk: resumable chunked uploads (files up to 2 GB, several devices at once).

``POST /drive/uploads`` → ``PUT /drive/uploads/{id}?offset=N`` (raw 8 MB chunks) → ``POST /drive/uploads/{id}/complete``.
After a lost connection the client asks ``GET /drive/uploads/{id}`` and continues from ``received``.
Chunks go straight to disk; the gate bounds concurrent writes / completions, unfinished uploads are capped per
person and per server, and the disk space they still need is reserved.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from infrastructure import blobs
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import LegalHubDriveFileModel, LegalHubDriveUploadModel
from presentation.deps import require_user
from presentation.routes.drive import (
    _ensure_folder_path,
    _file_out,
    _folder_in,
    _taken_names,
    _user_id,
    clean_name,
    load_tree,
    unique_name,
)

router = APIRouter(tags=["legal-hub"])

MAX_ACTIVE_PER_USER = 6
MAX_ACTIVE_TOTAL = 60


class UploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    size: int = Field(gt=0)
    contentType: str = Field(default="application/octet-stream", max_length=120)
    parentId: str | None = None
    path: str = Field(default="", max_length=2000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gb(value: int) -> str:
    return f"{value / (1024 ** 3):.1f} ГБ"


def _out(row: LegalHubDriveUploadModel) -> dict:
    return {"id": row.id, "name": row.name, "size": row.size_bytes, "received": row.received_bytes,
            "chunkSize": get_settings().drive_upload_chunk_bytes}


async def purge_stale_drive_uploads(session: AsyncSession) -> int:
    cutoff = _now() - timedelta(hours=get_settings().drive_upload_ttl_hours)
    stale = list(await session.scalars(select(LegalHubDriveUploadModel).where(LegalHubDriveUploadModel.updated_at < cutoff)))
    for row in stale:
        await run_in_threadpool(blobs.store.drop_part, row.id)
        await session.delete(row)
    if stale:
        await session.commit()
    return len(stale)


async def _own(session: AsyncSession, user: dict, upload_id: str) -> LegalHubDriveUploadModel:
    row = await session.get(LegalHubDriveUploadModel, upload_id)
    if row is None or row.user_id != _user_id(user):
        raise HTTPException(status_code=404, detail="Загрузка не найдена или устарела — начните заново")
    return row


@router.post("/drive/uploads", status_code=201)
async def start_drive_upload(body: UploadIn, session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    settings = get_settings()
    tree = await load_tree(session, user)
    parent = _folder_in(tree, body.parentId)
    tree.require(parent, "edit")
    if body.size > settings.drive_max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"Файл больше {_gb(settings.drive_max_upload_bytes)}")
    await purge_stale_drive_uploads(session)
    uid = _user_id(user)
    mine = await session.scalar(select(func.count()).select_from(LegalHubDriveUploadModel).where(LegalHubDriveUploadModel.user_id == uid)) or 0
    if mine >= MAX_ACTIVE_PER_USER:
        raise HTTPException(status_code=429, detail="Уже идёт несколько загрузок — новая начнётся, когда освободится место",
                            headers={"Retry-After": "10"})
    total, pending = (await session.execute(select(
        func.count(), func.coalesce(func.sum(LegalHubDriveUploadModel.size_bytes - LegalHubDriveUploadModel.received_bytes), 0),
    ).select_from(LegalHubDriveUploadModel))).one()
    if total >= MAX_ACTIVE_TOTAL:
        raise HTTPException(status_code=429, detail="Сервер сейчас принимает много файлов — загрузка начнётся автоматически",
                            headers={"Retry-After": "15"})
    free = await run_in_threadpool(blobs.store.free_bytes) - settings.drive_disk_reserve_bytes - int(pending or 0)
    if free < body.size:
        raise HTTPException(status_code=507, detail=f"На сервере не хватает места: свободно {_gb(max(0, free))}, "
                                                    f"нужно {_gb(body.size)}. Обратитесь к администратору.")
    try:
        name = clean_name(body.name.replace("\\", "/").rsplit("/", 1)[-1])
    except HTTPException:
        name = "file"
    stamp = _now()
    row = LegalHubDriveUploadModel(
        id=str(uuid4()), parent_id=parent, rel_path=body.path, user_id=uid, name=name,
        content_type=(body.contentType or "application/octet-stream")[:120], size_bytes=body.size, received_bytes=0,
        created_at=stamp, updated_at=stamp,
    )
    session.add(row)
    await session.commit()
    return _out(row)


@router.get("/drive/uploads/{upload_id}")
async def drive_upload_status(upload_id: str, session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    row = await _own(session, user, upload_id)
    on_disk = await run_in_threadpool(blobs.store.part_size, row.id)
    if on_disk != row.received_bytes:
        row.received_bytes = min(on_disk, row.size_bytes)
        await session.commit()
    return _out(row)


@router.put("/drive/uploads/{upload_id}")
async def drive_upload_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(ge=0),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    row = await _own(session, user, upload_id)
    async with blobs.gate.slot("write"):
        limit = get_settings().drive_upload_chunk_bytes * 2
        data = bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data) > limit:
                raise HTTPException(status_code=413, detail="Слишком большой фрагмент")
        if not data:
            raise HTTPException(status_code=400, detail="Пустой фрагмент")
        end = offset + len(data)
        if end > row.size_bytes:
            raise HTTPException(status_code=400, detail="Фрагмент выходит за размер файла")
        if offset > row.received_bytes:
            raise HTTPException(status_code=409, detail={"message": "Пропущен фрагмент", "received": row.received_bytes})
        if end <= row.received_bytes:
            return _out(row)
        row.received_bytes = await run_in_threadpool(blobs.store.append_chunk, row.id, offset, bytes(data))
        row.updated_at = _now()
        await session.commit()
        return _out(row)


@router.post("/drive/uploads/{upload_id}/complete")
async def complete_drive_upload(upload_id: str, session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    row = await _own(session, user, upload_id)
    on_disk = await run_in_threadpool(blobs.store.part_size, row.id)
    if row.received_bytes != row.size_bytes or on_disk != row.size_bytes:
        raise HTTPException(status_code=409, detail={"message": "Файл загружен не полностью", "received": on_disk})
    tree = await load_tree(session, user)
    parent = _folder_in(tree, row.parent_id)
    tree.require(parent, "edit")
    segments = [part for part in re.split(r"[\\/]+", row.rel_path or "") if part.strip()]
    folder_parts = [clean_name(part) for part in segments[:-1]]
    target = await _ensure_folder_path(session, tree, parent, folder_parts, user) if folder_parts else parent
    async with blobs.gate.slot("finish"):
        sha, size = await run_in_threadpool(blobs.store.adopt, blobs.store.part_path(row.id))
    name = unique_name(row.name, await _taken_names(session, target), is_file=True)
    stamp = _now()
    item = LegalHubDriveFileModel(
        id=str(uuid4()), folder_id=target, name=name, content_type=row.content_type, size_bytes=size,
        content=b"", blob_sha=sha, is_hidden=False, created_by_user_id=_user_id(user), created_at=stamp, updated_at=stamp,
    )
    session.add(item)
    await session.delete(row)
    await session.commit()
    return {"file": _file_out(item), "parentId": parent}


@router.delete("/drive/uploads/{upload_id}")
async def cancel_drive_upload(upload_id: str, session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    row = await _own(session, user, upload_id)
    await run_in_threadpool(blobs.store.drop_part, row.id)
    await session.delete(row)
    await session.commit()
    return {"ok": True}
