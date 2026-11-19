"""Versions and duplicates: content fingerprints, unchanged re-uploads, the same letter filed twice."""

from __future__ import annotations

import hashlib
from email import policy as email_policy
from email.parser import BytesHeaderParser

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from infrastructure import blobs
from infrastructure.models import DocumentFingerprintModel, DocumentModel, EmailFilingModel, VersionModel

_HEADER_BYTES = 65536


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalize_message_id(raw: str | None) -> str:
    return (raw or "").strip().strip("<>").strip().lower()[:500]


def eml_headers(content: bytes):
    try:
        return BytesHeaderParser(policy=email_policy.default).parsebytes(content[:_HEADER_BYTES])
    except Exception:
        return None


def message_id_from_eml(name: str, content: bytes) -> str:
    """Message-ID of an .eml (empty for other files or when the header is missing)."""
    if not name.lower().endswith(".eml"):
        return ""
    head = eml_headers(content)
    try:
        return normalize_message_id(str(head.get("Message-ID") or "")) if head is not None else ""
    except Exception:
        return ""


def subject_from_eml(content: bytes) -> str:
    """Subject header of an .eml (RFC 2047 decoded); empty when it cannot be parsed."""
    head = eml_headers(content)
    try:
        return " ".join(str(head.get("Subject") or "").split())[:500] if head is not None else ""
    except Exception:
        return ""


async def set_fingerprint(session: AsyncSession, doc: DocumentModel, content: bytes, *, name: str | None = None,
                          sha: str | None = None) -> DocumentFingerprintModel:
    """Record the fingerprint of the version just written (call after bumping current_version).

    For files stored on disk pass ``sha`` and only the first bytes as ``content`` (e-mail headers)."""
    row = await session.get(DocumentFingerprintModel, doc.id)
    message_id = message_id_from_eml(name or doc.name, content)
    sha = sha or digest(content)
    if row is None:
        row = DocumentFingerprintModel(document_id=doc.id, matter_id=doc.matter_id, version=doc.current_version,
                                       sha256=sha, message_id=message_id)
        session.add(row)
    else:
        row.matter_id = doc.matter_id
        row.version = doc.current_version
        row.sha256 = sha
        row.message_id = message_id or row.message_id
    return row


async def ensure_fingerprints(session: AsyncSession, docs: list[DocumentModel]) -> dict[str, DocumentFingerprintModel]:
    """Fingerprints of the current versions; missing or outdated ones are computed from stored content."""
    if not docs:
        return {}
    ids = [doc.id for doc in docs]
    rows = {row.document_id: row for row in await session.scalars(
        select(DocumentFingerprintModel).where(DocumentFingerprintModel.document_id.in_(ids))
    )}
    for doc in docs:
        row = rows.get(doc.id)
        if row is not None and row.version == doc.current_version and row.matter_id == doc.matter_id:
            continue
        version = await session.scalar(select(VersionModel).where(
            VersionModel.document_id == doc.id, VersionModel.version == doc.current_version,
        ).limit(1))
        if version is None:
            continue
        if version.blob_sha:
            head = await run_in_threadpool(blobs.read_head, version.blob_sha)
            rows[doc.id] = await set_fingerprint(session, doc, head, name=version.name, sha=version.blob_sha)
        else:
            rows[doc.id] = await set_fingerprint(session, doc, version.content, name=version.name)
    return rows


async def current_digest(session: AsyncSession, doc: DocumentModel) -> str:
    rows = await ensure_fingerprints(session, [doc])
    row = rows.get(doc.id)
    return row.sha256 if row else ""


async def find_same_content(session: AsyncSession, matter_id: str, sha: str) -> DocumentModel | None:
    """A live document of the matter whose current version has exactly this content."""
    return await session.scalar(
        select(DocumentModel)
        .join(DocumentFingerprintModel, DocumentFingerprintModel.document_id == DocumentModel.id)
        .where(
            DocumentFingerprintModel.matter_id == matter_id,
            DocumentFingerprintModel.sha256 == sha,
            DocumentFingerprintModel.version == DocumentModel.current_version,
            DocumentModel.deleted_at.is_(None),
        )
        .order_by(DocumentModel.created_at.asc())
        .limit(1)
    )


async def find_mail_document(
    session: AsyncSession,
    matter_id: str,
    *,
    kind: str,
    message_id: str,
    conversation_id: str,
    name: str,
) -> DocumentModel | None:
    """
    The document this letter already lives in within the matter:
    a thread by conversation, a letter by Message-ID (also letters filed before tracking existed),
    an attachment by Message-ID + file name.
    """
    mid = normalize_message_id(message_id)
    live = (DocumentModel.matter_id == matter_id, DocumentModel.deleted_at.is_(None))
    if kind == "thread":
        if not conversation_id.strip():
            return None
        return await session.scalar(
            select(DocumentModel)
            .join(EmailFilingModel, EmailFilingModel.document_id == DocumentModel.id)
            .where(*live, EmailFilingModel.kind == "thread", EmailFilingModel.conversation_id == conversation_id.strip())
            .order_by(DocumentModel.created_at.asc())
            .limit(1)
        )
    if not mid:
        return None
    if kind == "attachment":
        return await session.scalar(
            select(DocumentModel)
            .join(EmailFilingModel, EmailFilingModel.document_id == DocumentModel.id)
            .where(*live, EmailFilingModel.kind == "attachment", EmailFilingModel.message_id == mid,
                   func.lower(DocumentModel.name) == name.lower())
            .limit(1)
        )
    by_filing = select(EmailFilingModel.document_id).where(EmailFilingModel.kind == "email", EmailFilingModel.message_id == mid)
    by_header = select(DocumentFingerprintModel.document_id).where(
        DocumentFingerprintModel.matter_id == matter_id, DocumentFingerprintModel.message_id == mid,
    )
    return await session.scalar(
        select(DocumentModel)
        .where(*live, or_(DocumentModel.id.in_(by_filing), DocumentModel.id.in_(by_header)))
        .order_by(DocumentModel.created_at.asc())
        .limit(1)
    )


async def backfill_fingerprints(session: AsyncSession, matter_id: str, *, mail_only: bool = False, limit: int = 2000) -> int:
    """Fingerprint documents of a matter that predate fingerprints (one-time cost per matter)."""
    query = (
        select(DocumentModel)
        .outerjoin(DocumentFingerprintModel, DocumentFingerprintModel.document_id == DocumentModel.id)
        .where(DocumentModel.matter_id == matter_id, DocumentModel.deleted_at.is_(None),
               DocumentFingerprintModel.document_id.is_(None))
    )
    if mail_only:
        query = query.where(func.lower(DocumentModel.name).like("%.eml"))
    docs = list(await session.scalars(query.limit(limit)))
    await ensure_fingerprints(session, docs)
    return len(docs)


async def store_content(content: bytes) -> str:
    """Put uploaded bytes into the on-disk store; returns the sha256 used as the blob id."""
    return await run_in_threadpool(blobs.put_bytes, content)


async def version_head(version: VersionModel) -> bytes:
    """First bytes of a version (e-mail headers), wherever it is stored."""
    if version.blob_sha:
        return await run_in_threadpool(blobs.read_head, version.blob_sha)
    return (version.content or b"")[:_HEADER_BYTES]
