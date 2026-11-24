"""Duplicates inside a matter: identical files and the same letter filed more than once."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.models import DocumentFingerprintModel, DocumentModel
from presentation.common import crumbs, doc_out, matter_folders, doc_scope, require_matter
from presentation.deps import Viewer, get_viewer
from presentation.versioning import backfill_fingerprints

router = APIRouter(tags=["file-manager"])


@router.get("/matters/{matter_id}/duplicates")
async def matter_duplicates(
    matter_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """
    Groups of live documents that are copies of each other, oldest first (the one to keep):
    ``content`` — byte-identical current versions, ``message`` — the same e-mail (Message-ID).
    """
    dirx, matter, _ = await require_matter(session, viewer, matter_id, "read")
    await backfill_fingerprints(session, matter.id)
    await session.commit()
    rows = (await session.execute(
        select(DocumentModel, DocumentFingerprintModel)
        .join(DocumentFingerprintModel, DocumentFingerprintModel.document_id == DocumentModel.id)
        .where(DocumentModel.matter_id == matter.id, DocumentModel.deleted_at.is_(None),
               DocumentFingerprintModel.version == DocumentModel.current_version, *(await doc_scope(session, viewer, "read", {matter.id})))
        .order_by(DocumentModel.created_at.asc(), DocumentModel.doc_number.asc())
    )).all()

    by_hash: dict[str, list[DocumentModel]] = defaultdict(list)
    by_message: dict[str, list[DocumentModel]] = defaultdict(list)
    for doc, fp in rows:
        by_hash[fp.sha256].append(doc)
        if fp.message_id:
            by_message[fp.message_id].append(doc)

    folders = await matter_folders(session, matter.id)

    def out(doc: DocumentModel) -> dict:
        return {**doc_out(doc, dirx), "folderPath": crumbs(folders, doc.folder_id)}

    groups: list[dict] = []
    covered: set[frozenset[str]] = set()
    for key, docs in by_hash.items():
        if len(docs) > 1:
            covered.add(frozenset(doc.id for doc in docs))
            groups.append({"reason": "content", "key": key[:12], "documents": [out(doc) for doc in docs]})
    for key, docs in by_message.items():
        ids = frozenset(doc.id for doc in docs)
        if len(docs) > 1 and not any(ids <= group for group in covered):
            groups.append({"reason": "message", "key": key, "documents": [out(doc) for doc in docs]})
    groups.sort(key=lambda group: group["documents"][0]["createdAt"] or "")
    return {"groups": groups, "total": sum(len(group["documents"]) - 1 for group in groups)}
