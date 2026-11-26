"""Previews of matter documents: what is inside a ZIP, single files from it, e-mails (.eml) with attachments."""

from __future__ import annotations

import io
from collections.abc import Callable
from email.parser import BytesHeaderParser
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
from pydantic import BaseModel, Field
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from infrastructure import blobs
from infrastructure.database import get_session
from infrastructure.models import DocumentModel, EmailFilingModel, FolderModel, VersionModel
from presentation.common import (
    audit,
    crumbs,
    doc_out,
    document_allowed,
    ensure_folder_open,
    folder_in_matter,
    iso,
    live_document,
    next_doc_number,
    now,
    require_matter,
)
from presentation.intake import file_into_matter
from presentation.routes.mail import new_email_filing, normalize_message_id
from presentation.versioning import digest, store_content
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


def _letter_key(data: bytes, doc: DocumentModel) -> str:
    """Message-ID of the letter (what links its attachments to the documents made from them)."""
    try:
        headers = BytesHeaderParser().parsebytes(data)
        mid = normalize_message_id(str(headers.get("Message-ID") or ""))
    except Exception:  # noqa: BLE001 — a broken header must not break the preview
        mid = ""
    return mid or f"fm-doc:{doc.id}"


async def _saved_attachments(session: AsyncSession, viewer: Viewer, doc: DocumentModel, key: str) -> dict[str, dict]:
    """Attachments of this letter already saved into the matter (by anyone): file name → where it is."""
    rows = (await session.execute(
        select(DocumentModel, EmailFilingModel)
        .join(EmailFilingModel, EmailFilingModel.document_id == DocumentModel.id)
        .where(
            EmailFilingModel.kind == "attachment",
            EmailFilingModel.message_id == key,
            DocumentModel.matter_id == doc.matter_id,
            DocumentModel.deleted_at.is_(None),
        )
        .order_by(EmailFilingModel.filed_at.asc())
    )).all()
    if not rows:
        return {}
    folders = list(await session.scalars(select(FolderModel).where(FolderModel.matter_id == doc.matter_id)))
    out: dict[str, dict] = {}
    for saved, filing in rows:
        # Saved from the preview the original attachment name is in ``subject`` (the document may be renamed).
        names = {saved.name.lower(), (filing.subject or "").strip().lower()} - {""}
        if all(name in out for name in names):
            continue
        entry = {
            "documentId": saved.id,
            "name": saved.name,
            "folderId": saved.folder_id,
            "folderPath": " / ".join(crumb["name"] for crumb in crumbs(folders, saved.folder_id)) if saved.folder_id else "",
            "savedBy": filing.filed_by_name,
            "savedAt": iso(filing.filed_at),
            "visible": await document_allowed(session, viewer, saved),
        }
        for name in names:
            out.setdefault(name, entry)
    return out


async def _free_name(session: AsyncSession, matter_id: str, folder_id: str | None, name: str) -> str:
    """`pdf.pdf` → `pdf (2).pdf` when another live document of the folder already has that name."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    taken = {
        value.lower() for value in await session.scalars(
            select(DocumentModel.name).where(
                DocumentModel.matter_id == matter_id,
                DocumentModel.deleted_at.is_(None),
                DocumentModel.folder_id.is_(None) if folder_id is None else DocumentModel.folder_id == folder_id,
            )
        )
    }
    candidate, number = name, 1
    while candidate.lower() in taken:
        number += 1
        candidate = f"{stem} ({number}){'.' + ext if ext else ''}"
    return candidate


@router.get("/documents/{document_id}/email")
async def email_preview(
    document_id: str,
    version: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc, row = await readable_version(session, viewer, document_id, version)
    data = await email_bytes(opener(row), row.size_bytes)
    parsed = await run_in_threadpool(parse_eml, data)
    saved = await _saved_attachments(session, viewer, doc, _letter_key(data, doc))
    for item in parsed.get("attachments") or []:
        item["saved"] = saved.get(str(item.get("name") or "").lower())
    return parsed


class SaveAttachmentIn(BaseModel):
    folderId: str | None = Field(default=None, max_length=36)


@router.post("/documents/{document_id}/email/{index}/save")
async def save_email_attachment(
    document_id: str,
    index: int,
    body: SaveAttachmentIn,
    version: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """
    Save one attachment of a filed letter as a document of the same matter (by default — next to the letter).
    Saved once: everyone then sees where it is instead of a download button.
    """
    doc, row = await readable_version(session, viewer, document_id, version)
    dirx, matter, _ = await require_matter(session, viewer, doc.matter_id, "edit")
    data = await email_bytes(opener(row), row.size_bytes)
    try:
        name, media, payload = await run_in_threadpool(eml_attachment, data, index)
    except KeyError:
        raise HTTPException(status_code=404, detail="Вложение не найдено") from None
    folder_id = body.folderId if body.folderId is not None else doc.folder_id
    folder = await folder_in_matter(session, matter.id, folder_id) if folder_id else None
    await ensure_folder_open(session, viewer, folder)
    key = _letter_key(data, doc)
    already = (await _saved_attachments(session, viewer, doc, key)).get(name.lower())
    if already is not None:
        existing = await session.get(DocumentModel, already["documentId"])
        if existing is not None:
            return {
                "status": "exists",
                "document": doc_out(existing, dirx),
                "folderId": existing.folder_id,
                "folderPath": already["folderPath"],
                "savedBy": already["savedBy"],
                "visible": already["visible"],
            }
    original = name
    # A file of the same name from another letter must not turn into a version of this attachment.
    name = await _free_name(session, matter.id, folder.id if folder else None, name)
    stamp = now()
    first = await next_doc_number(session)
    numbers = iter(range(first, first + 10))

    async def next_number() -> int:
        return next(numbers)

    async def store() -> None:
        await store_content(payload)

    status, saved = await file_into_matter(
        session, viewer, matter,
        folder_id=folder.id if folder else None,
        name=name,
        content_type=media or "application/octet-stream",
        size=len(payload),
        sha=digest(payload),
        head=payload[:65536],
        comment=f"Вложение из письма «{doc.name}»"[:500],
        allow_duplicate=False,
        stamp=stamp,
        next_number=next_number,
        store=store,
        is_mail=True,
        kind="attachment",
        mail_message_id=key,
    )
    if status in ("created", "version", "duplicate", "unchanged"):
        session.add(new_email_filing(
            viewer, saved, message_id=key, conversation_id="", from_email="", subject=original,
            kind="attachment", stamp=stamp,
        ))
        audit(session, viewer, "attachment_save", matter=matter, kind="document", target_id=saved.id,
              target_name=saved.name, detail=f"from {doc.id}")
    await session.commit()
    path = ""
    if saved.folder_id:
        folders = list(await session.scalars(select(FolderModel).where(FolderModel.matter_id == saved.matter_id)))
        path = " / ".join(crumb["name"] for crumb in crumbs(folders, saved.folder_id))
    return {
        "status": status,
        "document": doc_out(saved, dirx),
        "folderId": saved.folder_id,
        "folderPath": path,
        "savedBy": viewer.name if status in ("created", "version", "duplicate", "unchanged") else "",
        "visible": await document_allowed(session, viewer, saved),
    }


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
