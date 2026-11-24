"""Resumable chunked uploads for large files (gigabytes).

Flow: ``POST /matters/{id}/uploads`` → ``PUT /uploads/{id}?offset=N`` (raw chunk, repeat) →
``POST /uploads/{id}/complete``. After a dropped connection the client asks ``GET /uploads/{id}`` and
continues from ``received``. Chunks are written straight to disk; nothing holds the whole file in memory.
Abandoned uploads are removed after ``upload_session_ttl_hours``.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from application.access import safe_name
from infrastructure import blobs
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import UploadSessionModel
from presentation.common import (
    doc_out,
    ensure_folder_open,
    folder_in_matter,
    live_document,
    next_doc_number,
    now,
    require_document_edit,
    require_matter,
)
from presentation.deps import Viewer, get_viewer
from presentation.intake import file_into_matter

router = APIRouter(tags=["uploads"])

MAX_ACTIVE_PER_USER = 6
MAX_ACTIVE_TOTAL = 60


class UploadInit(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    size: int = Field(gt=0)
    contentType: str = Field(default="application/octet-stream", max_length=120)
    folderId: str | None = None
    documentId: str | None = None
    comment: str = Field(default="", max_length=500)
    allowDuplicate: bool = False


def _out(row: UploadSessionModel) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "size": row.size_bytes,
        "received": row.received_bytes,
        "chunkSize": get_settings().upload_chunk_bytes,
    }


def _gb(value: int) -> str:
    return f"{value / (1024 ** 3):.1f} ГБ"


async def purge_stale_uploads(session: AsyncSession) -> int:
    """Drop uploads nobody touched for the TTL (closed tabs, lost connections)."""
    cutoff = now() - timedelta(hours=get_settings().upload_session_ttl_hours)
    stale = list(await session.scalars(select(UploadSessionModel).where(UploadSessionModel.updated_at < cutoff)))
    for row in stale:
        await run_in_threadpool(blobs.drop_part, row.id)
        await session.delete(row)
    if stale:
        await session.commit()
    return len(stale)


async def _own(session: AsyncSession, viewer: Viewer, upload_id: str) -> UploadSessionModel:
    row = await session.get(UploadSessionModel, upload_id)
    if row is None or row.user_id != viewer.user_id:
        raise HTTPException(status_code=404, detail="Загрузка не найдена или устарела — начните заново")
    return row


@router.post("/matters/{matter_id}/uploads", status_code=201)
async def start_upload(
    matter_id: str,
    body: UploadInit,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    settings = get_settings()
    _, matter, _ = await require_matter(session, viewer, matter_id, "edit")
    folder = await folder_in_matter(session, matter.id, body.folderId)
    await ensure_folder_open(session, viewer, folder)
    if body.documentId:
        doc = await live_document(session, body.documentId, viewer)
        await require_document_edit(session, viewer, doc)
        if doc.matter_id != matter.id:
            raise HTTPException(status_code=400, detail="Документ из другого дела")
    if body.size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"Файл больше {_gb(settings.max_upload_bytes)}")
    await purge_stale_uploads(session)
    mine = await session.scalar(select(func.count()).select_from(UploadSessionModel).where(UploadSessionModel.user_id == viewer.user_id)) or 0
    if mine >= MAX_ACTIVE_PER_USER:
        raise HTTPException(status_code=429, detail="Уже идёт несколько загрузок — новая начнётся, когда освободится место",
                            headers={"Retry-After": "10"})
    total, pending = (await session.execute(select(
        func.count(), func.coalesce(func.sum(UploadSessionModel.size_bytes - UploadSessionModel.received_bytes), 0),
    ).select_from(UploadSessionModel))).one()
    if total >= MAX_ACTIVE_TOTAL:
        raise HTTPException(status_code=429, detail="Сервер сейчас принимает много файлов — загрузка начнётся автоматически",
                            headers={"Retry-After": "15"})
    # Space already promised to unfinished uploads (other devices) counts as used.
    free = await run_in_threadpool(blobs.free_bytes) - settings.disk_reserve_bytes - int(pending or 0)
    if free < body.size:
        raise HTTPException(
            status_code=507,
            detail=f"На сервере не хватает места: свободно {_gb(max(0, free))}, "
                   f"нужно {_gb(body.size)}. Обратитесь к администратору.",
        )
    stamp = now()
    row = UploadSessionModel(
        id=str(uuid4()),
        matter_id=matter.id,
        folder_id=folder.id if folder else None,
        document_id=body.documentId or None,
        user_id=viewer.user_id,
        name=safe_name(body.name),
        content_type=(body.contentType or "application/octet-stream")[:120],
        size_bytes=body.size,
        received_bytes=0,
        comment=body.comment.strip()[:500],
        allow_duplicate=1 if body.allowDuplicate else 0,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(row)
    await session.commit()
    return _out(row)


@router.get("/uploads/{upload_id}")
async def upload_status(
    upload_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    row = await _own(session, viewer, upload_id)
    # The file on disk is the truth (a chunk may have landed while the response was lost).
    on_disk = await run_in_threadpool(blobs.part_size, row.id)
    if on_disk != row.received_bytes:
        row.received_bytes = min(on_disk, row.size_bytes)
        await session.commit()
    return _out(row)


@router.put("/uploads/{upload_id}")
async def upload_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(ge=0),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    row = await _own(session, viewer, upload_id)
    async with blobs.gate.slot("write"):
        return await _write_chunk(session, row, request, offset)


async def _write_chunk(session: AsyncSession, row: UploadSessionModel, request: Request, offset: int) -> dict:
    limit = get_settings().upload_chunk_bytes * 2
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
        return _out(row)  # the same chunk sent again after a lost response
    row.received_bytes = await run_in_threadpool(blobs.append_chunk, row.id, offset, bytes(data))
    row.updated_at = now()
    await session.commit()
    return _out(row)


@router.post("/uploads/{upload_id}/complete")
async def complete_upload(
    upload_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    row = await _own(session, viewer, upload_id)
    on_disk = await run_in_threadpool(blobs.part_size, row.id)
    if row.received_bytes != row.size_bytes or on_disk != row.size_bytes:
        raise HTTPException(status_code=409, detail={"message": "Файл загружен не полностью", "received": on_disk})
    dirx, matter, _ = await require_matter(session, viewer, row.matter_id, "edit")
    folder = await folder_in_matter(session, matter.id, row.folder_id)
    target = await live_document(session, row.document_id, viewer) if row.document_id else None
    if target is not None:
        await require_document_edit(session, viewer, target)
    # Hash + move into the store (seconds for gigabytes; runs off the event loop).
    async with blobs.gate.slot("finish"):
        sha, size = await run_in_threadpool(blobs.adopt, blobs.part_path(row.id))
    head = await run_in_threadpool(blobs.read_head, sha)
    counter = {"next": await next_doc_number(session)}

    async def next_number() -> int:
        value = counter["next"]
        counter["next"] += 1
        return value

    status, doc = await file_into_matter(
        session, viewer, matter,
        folder_id=folder.id if folder else None,
        name=row.name,
        content_type=row.content_type,
        size=size,
        sha=sha,
        head=head,
        comment=row.comment,
        allow_duplicate=bool(row.allow_duplicate),
        stamp=now(),
        next_number=next_number,
        target=target,
    )
    await session.delete(row)
    await session.commit()
    document = doc_out(doc, dirx)
    return {"documents": [document], "results": [{"status": status, "document": document}]}


@router.delete("/uploads/{upload_id}")
async def cancel_upload(
    upload_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    row = await _own(session, viewer, upload_id)
    await run_in_threadpool(blobs.drop_part, row.id)
    await session.delete(row)
    await session.commit()
    return {"ok": True}
