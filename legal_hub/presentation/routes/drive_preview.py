"""Disk previews: inside a ZIP, single files from it, e-mails (.eml) with attachments."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import BinaryIO
from urllib.parse import quote

from backend_common.archive_view import MAX_EMAIL_BYTES, eml_attachment, list_zip, parse_eml, stream_zip_entry, zip_entry
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from infrastructure import blobs
from infrastructure.database import get_session
from infrastructure.models import LegalHubDriveFileModel
from presentation.deps import require_user
from presentation.routes.drive import load_tree

router = APIRouter(tags=["legal-hub"])


async def readable_file(session: AsyncSession, user: dict, file_id: str) -> LegalHubDriveFileModel:
    item = await session.get(LegalHubDriveFileModel, file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    (await load_tree(session, user)).require(item.folder_id, "read")
    return item


def opener(item: LegalHubDriveFileModel) -> Callable[[], BinaryIO]:
    if item.blob_sha:
        path = blobs.store.blob_path(item.blob_sha)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Файл не найден в хранилище")
        return lambda: open(path, "rb")
    content = item.content or b""
    return lambda: io.BytesIO(content)


def _list(open_source: Callable[[], BinaryIO]) -> dict:
    with open_source() as source:
        return list_zip(source)


def _meta(open_source: Callable[[], BinaryIO], index: int) -> tuple[str, str, int]:
    with open_source() as source:
        return zip_entry(source, index)


async def entry_response(open_source: Callable[[], BinaryIO], index: int, *, inline: bool) -> StreamingResponse:
    try:
        name, media, size = await run_in_threadpool(_meta, open_source, index)
    except KeyError:
        raise HTTPException(status_code=404, detail="В архиве нет такого файла") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return StreamingResponse(iterate_in_threadpool(stream_zip_entry(open_source, index)), media_type=media, headers={
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
        "Content-Length": str(size),
        "Cache-Control": "private, no-store",
    })


async def _email_bytes(item: LegalHubDriveFileModel) -> bytes:
    if item.size_bytes > MAX_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Письмо слишком большое для просмотра — скачайте его")
    open_source = opener(item)

    def read() -> bytes:
        with open_source() as source:
            return source.read()

    return await run_in_threadpool(read)


@router.get("/drive/files/{file_id}/archive")
async def drive_archive(file_id: str, session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    return await run_in_threadpool(_list, opener(await readable_file(session, user, file_id)))


@router.get("/drive/files/{file_id}/archive/{index}")
async def drive_archive_entry(file_id: str, index: int, inline: bool = Query(default=False),
                              session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    return await entry_response(opener(await readable_file(session, user, file_id)), index, inline=inline)


@router.get("/drive/files/{file_id}/email")
async def drive_email(file_id: str, session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    return await run_in_threadpool(parse_eml, await _email_bytes(await readable_file(session, user, file_id)))


@router.get("/drive/files/{file_id}/email/{index}")
async def drive_email_attachment(file_id: str, index: int, inline: bool = Query(default=False),
                                 session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    data = await _email_bytes(await readable_file(session, user, file_id))
    try:
        name, media, payload = await run_in_threadpool(eml_attachment, data, index)
    except KeyError:
        raise HTTPException(status_code=404, detail="Вложение не найдено") from None
    return Response(content=payload, media_type=media, headers={
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store",
    })
