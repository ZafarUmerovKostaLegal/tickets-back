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
from presentation.common import audit, ensure_not_locked
from presentation.deps import Viewer
from presentation.versioning import current_digest, find_mail_document, find_same_content, set_fingerprint


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
    if existing is None and is_mail:
        existing = await find_mail_document(session, matter.id, kind=kind, message_id=mail_message_id,
                                            conversation_id=mail_conversation_id, name=name)
        if existing is not None and kind != "thread":
            return "exists", existing
    if existing is None:
        query = select(DocumentModel).where(
            DocumentModel.matter_id == matter.id,
            DocumentModel.deleted_at.is_(None),
            func.lower(DocumentModel.name) == name.lower(),
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
            twin = await find_same_content(session, matter.id, sha)
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
