"""Putting one stored file into a matter: new document, new version, or nothing (same content).

Shared by the regular multipart upload and the resumable chunked upload. The content is already in the
on-disk store (``sha``); only metadata goes to the database.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import DocumentModel, MatterWorkspaceModel, VersionModel
from presentation.common import audit, ensure_not_locked, my_folder_levels, scope_clause
from presentation.deps import Viewer
from presentation.versioning import (
    current_digest,
    find_conversation_document,
    find_mail_document,
    find_same_content,
    set_fingerprint,
)


def eml_message_id(head: bytes) -> str:
    """Message-ID from the headers of an .eml (they are at the very start of the file)."""
    from email.parser import BytesHeaderParser

    from presentation.routes.mail import normalize_message_id

    try:
        return normalize_message_id(str(BytesHeaderParser().parsebytes(head).get("Message-ID") or ""))
    except Exception:  # noqa: BLE001 — not a letter after all
        return ""


async def file_into_matter(
    session: AsyncSession,
    viewer: Viewer,
    matter: MatterWorkspaceModel,
    *,
    folder_id: str | None,
    name: str,
    content_type: str,
    size: int,
    sha: str,
    head: bytes,
    comment: str,
    allow_duplicate: bool,
    stamp: datetime,
    next_number: Callable[[], Awaitable[int]],
    store: Callable[[], Awaitable[None]] | None = None,
    target: DocumentModel | None = None,
    is_mail: bool = False,
    kind: str = "email",
    mail_message_id: str = "",
    mail_conversation_id: str = "",
) -> tuple[str, DocumentModel]:
    """Returns (status, document); status is created | version | unchanged | exists | duplicate.

    ``store`` writes the blob and is only awaited when a version is actually created.
    ``target`` forces a new version of that document (check-in of a large file)."""
    existing: DocumentModel | None = target
    folder_map = await my_folder_levels(session, viewer, {matter.id})
    visible = scope_clause(viewer, folder_map)
    editable = scope_clause(viewer, folder_map, "edit")
    if existing is None and not is_mail and not allow_duplicate and name.lower().endswith(".eml"):
        # A letter dragged into the Hub: the same Message-ID filed by anyone in the matter means it is already there.
        header_id = eml_message_id(head)
        if header_id:
            filed = await find_mail_document(session, matter.id, kind="email", message_id=header_id,
                                             conversation_id="", name=name)
            if filed is not None:
                return "exists", filed
    if existing is None and is_mail:
        # One copy of a letter (or attachment) per matter, whoever filed it first: colleagues who also got
        # the message do not file their own copy — a partner opens access to the existing one when needed.
        existing = await find_mail_document(session, matter.id, kind=kind, message_id=mail_message_id,
                                            conversation_id=mail_conversation_id, name=name,
                                            scope=() if kind != "thread" else visible)
        if existing is not None and kind != "thread":
            return "exists", existing
        if existing is None and kind == "email":
            # A reply in a correspondence that is already filed: one document, the newest letter on top.
            existing = await find_conversation_document(session, matter.id, mail_conversation_id, editable)
            if existing is not None and existing.checked_out_by_user_id not in (None, viewer.user_id):
                existing = None
    if existing is None:
        query = select(DocumentModel).where(
            DocumentModel.matter_id == matter.id,
            DocumentModel.deleted_at.is_(None),
            func.lower(DocumentModel.name) == name.lower(),
            *editable,
        )
        query = query.where(DocumentModel.folder_id.is_(None) if folder_id is None else DocumentModel.folder_id == folder_id)
        existing = await session.scalar(query.limit(1))

    if existing is not None:
        ensure_not_locked(existing, viewer)
        if await current_digest(session, existing) == sha:
            return "unchanged", existing
        existing.current_version += 1
        existing.size_bytes = size
        existing.content_type = content_type
        existing.updated_at = stamp
        existing.updated_by_name = viewer.name
        doc = existing
        status = "version"
        audit(session, viewer, "version", matter=matter, kind="document", target_id=doc.id,
              target_name=doc.name, detail=f"v{doc.current_version}")
    else:
        if not is_mail and not allow_duplicate:
            twin = await find_same_content(session, matter.id, sha, visible)
            if twin is not None:
                return "duplicate", twin
        doc = DocumentModel(
            id=str(uuid4()),
            doc_number=await next_number(),
            matter_id=matter.id,
            client_id=matter.client_id,
            folder_id=folder_id,
            name=name,
            current_version=1,
            content_type=content_type,
            size_bytes=size,
            created_by_user_id=viewer.user_id,
            created_by_name=viewer.name,
            created_at=stamp,
            updated_by_name=viewer.name,
            updated_at=stamp,
        )
        session.add(doc)
        status = "created"
        audit(session, viewer, "upload", matter=matter, kind="document", target_id=doc.id, target_name=name)
    if store is not None:
        await store()
    session.add(VersionModel(
        id=str(uuid4()),
        document_id=doc.id,
        version=doc.current_version,
        name=name,
        content_type=content_type,
        size_bytes=size,
        content=b"",
        blob_sha=sha,
        comment=(comment or "").strip()[:500],
        created_by_user_id=viewer.user_id,
        created_by_name=viewer.name,
        created_at=stamp,
    ))
    await set_fingerprint(session, doc, head, name=name, sha=sha)
    return status, doc


async def blob_in_use(session: AsyncSession, sha: str) -> bool:
    return (await session.scalar(select(VersionModel.id).where(VersionModel.blob_sha == sha).limit(1))) is not None
