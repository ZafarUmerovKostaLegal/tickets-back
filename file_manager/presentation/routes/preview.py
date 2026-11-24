"""Previews of matter documents: what is inside a ZIP, single files from it, e-mails (.eml) with attachments."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import BinaryIO
from urllib.parse import quote

from backend_common.archive_view import (
    MAX_EMAIL_BYTES,
    eml_attachment,
    list_zip,
    parse_eml,
    stream_zip_entry,
    zip_entry,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from infrastructure import blobs
from infrastructure.database import get_session
from infrastructure.models import DocumentModel, VersionModel
from presentation.common import live_document, require_matter
from presentation.deps import Viewer, get_viewer

router = APIRouter(tags=["preview"])


async def readable_version(session: AsyncSession, viewer: Viewer, document_id: str, version: int | None) -> tuple[DocumentModel, VersionModel]:
    doc = await live_document(session, document_id, viewer)
    await require_matter(session, viewer, doc.matter_id, "read")
    wanted = version or doc.current_version
    row = await session.scalar(
        select(VersionModel).where(VersionModel.document_id == doc.id, VersionModel.version == wanted).limit(1)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    return doc, row


def opener(row: VersionModel) -> Callable[[], BinaryIO]:
    if row.blob_sha:
        path = blobs.blob_path(row.blob_sha)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Файл не найден в хранилище")
        return lambda: open(path, "rb")
    content = row.content or b""
    return lambda: io.BytesIO(content)


def _list(open_source: Callable[[], BinaryIO]) -> dict:
    with open_source() as source:
        return list_zip(source)


def _entry_meta(open_source: Callable[[], BinaryIO], index: int) -> tuple[str, str, int]:
    with open_source() as source:
        return zip_entry(source, index)


async def entry_response(open_source: Callable[[], BinaryIO], index: int, *, inline: bool) -> StreamingResponse:
    try:
        name, media, size = await run_in_threadpool(_entry_meta, open_source, index)
    except KeyError:
        raise HTTPException(status_code=404, detail="В архиве нет такого файла") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return StreamingResponse(
        iterate_in_threadpool(stream_zip_entry(open_source, index)),
        media_type=media,
        headers={
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
            "Content-Length": str(size),
            "Cache-Control": "private, no-store",
        },
    )


async def email_bytes(open_source: Callable[[], BinaryIO], size: int) -> bytes:
    if size > MAX_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Письмо слишком большое для просмотра — скачайте его")

    def read() -> bytes:
        with open_source() as source:
            return source.read()

    return await run_in_threadpool(read)


@router.get("/documents/{document_id}/archive")
async def archive_listing(
    document_id: str,
    version: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    _, row = await readable_version(session, viewer, document_id, version)
    return await run_in_threadpool(_list, opener(row))


@router.get("/documents/{document_id}/archive/{index}")
async def archive_entry(
    document_id: str,
    index: int,
    version: int | None = Query(default=None, ge=1),
    inline: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    _, row = await readable_version(session, viewer, document_id, version)
    return await entry_response(opener(row), index, inline=inline)


@router.get("/documents/{document_id}/email")
async def email_preview(
    document_id: str,
    version: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    _, row = await readable_version(session, viewer, document_id, version)
    data = await email_bytes(opener(row), row.size_bytes)
    return await run_in_threadpool(parse_eml, data)


@router.get("/documents/{document_id}/email/{index}")
async def email_attachment(
    document_id: str,
    index: int,
    version: int | None = Query(default=None, ge=1),
    inline: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    _, row = await readable_version(session, viewer, document_id, version)
    data = await email_bytes(opener(row), row.size_bytes)
    try:
        name, media, payload = await run_in_threadpool(eml_attachment, data, index)
    except KeyError:
        raise HTTPException(status_code=404, detail="Вложение не найдено") from None
    return Response(content=payload, media_type=media, headers={
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store",
    })
