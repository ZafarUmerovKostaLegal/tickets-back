"""Outlook add-in support: where a message was filed and where to file the next one (MacroView-style)."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import has_level
from infrastructure.database import get_session
from infrastructure.models import DocumentModel, EmailFilingModel, FolderModel
from presentation.common import crumbs, directory, iso, levels, matter_closed, now
from presentation.deps import Viewer, get_viewer

router = APIRouter(tags=["file-manager"])

FILING_KINDS = frozenset({"email", "attachment", "thread"})

# Free mail providers and our own domain say nothing about the matter.
_GENERIC_DOMAINS = frozenset({
    "kostalegal.com",
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "icloud.com", "me.com", "yahoo.com", "aol.com", "proton.me", "protonmail.com",
    "mail.ru", "inbox.ru", "list.ru", "bk.ru", "yandex.ru", "ya.ru", "yandex.com", "rambler.ru",
})

_REASON_RANK = {"conversation": 4, "sender": 3, "domain": 2, "recent": 1}
_RECENT_DAYS = 60


def normalize_message_id(raw: str | None) -> str:
    return (raw or "").strip().strip("<>").strip().lower()[:500]


def normalize_email(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if "<" in value and ">" in value:
        value = value[value.index("<") + 1:value.index(">")].strip()
    return value[:320]


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[1] if "@" in email else ""


def new_email_filing(
    viewer: Viewer,
    doc: DocumentModel,
    *,
    message_id: str,
    conversation_id: str,
    from_email: str,
    subject: str,
    kind: str,
    stamp: datetime,
) -> EmailFilingModel:
    sender = normalize_email(from_email)
    return EmailFilingModel(
        id=str(uuid4()),
        message_id=normalize_message_id(message_id),
        conversation_id=(conversation_id or "").strip()[:300],
        from_email=sender,
        from_domain=email_domain(sender)[:255],
        subject=(subject or "").strip()[:500],
        kind=kind if kind in FILING_KINDS else "email",
        document_id=doc.id,
        matter_id=doc.matter_id,
        client_id=doc.client_id,
        folder_id=doc.folder_id,
        filed_by_user_id=viewer.user_id,
        filed_by_name=viewer.name,
        filed_at=stamp,
    )


async def _folder_paths(session: AsyncSession, folder_ids: set[str]) -> dict[str, list[dict]]:
    """Breadcrumbs for live folders; deleted folders are missing from the result."""
    if not folder_ids:
        return {}
    matter_ids = set(await session.scalars(select(FolderModel.matter_id).where(FolderModel.id.in_(folder_ids))))
    if not matter_ids:
        return {}
    folders = list(await session.scalars(
        select(FolderModel).where(FolderModel.matter_id.in_(matter_ids), FolderModel.deleted_at.is_(None))
    ))
    live = {folder.id for folder in folders}
    return {fid: crumbs(folders, fid) for fid in folder_ids if fid in live}


def _location(dirx, matter_id: str, folder_id: str | None, paths: dict[str, list[dict]]) -> dict:
    matter = dirx.matters.get(matter_id)
    client = dirx.clients.get(matter.client_id) if matter else None
    return {
        "matterId": matter_id,
        "matterName": matter.name if matter else "",
        "matterCode": matter.code if matter else "",
        "clientId": matter.client_id if matter else "",
        "clientName": client.name if client else "",
        "folderId": folder_id,
        "folderPath": paths.get(folder_id, []) if folder_id else [],
    }


class LookupIn(BaseModel):
    messageIds: list[str] = Field(default_factory=list, max_length=100)
    conversationId: str = Field(default="", max_length=300)


@router.post("/mail/lookup")
async def lookup_filings(
    body: LookupIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Filings of these messages (and of their thread) in matters the viewer can read."""
    ids = sorted({normalize_message_id(raw) for raw in body.messageIds} - {""})
    conversation = body.conversationId.strip()
    if not ids and not conversation:
        return {"filings": []}
    terms = []
    if ids:
        terms.append(EmailFilingModel.message_id.in_(ids))
    if conversation:
        terms.append(EmailFilingModel.conversation_id == conversation)
    rows = (await session.execute(
        select(EmailFilingModel, DocumentModel)
        .join(DocumentModel, DocumentModel.id == EmailFilingModel.document_id)
        .where(or_(*terms), DocumentModel.deleted_at.is_(None))
        .order_by(EmailFilingModel.filed_at.desc())
        .limit(300)
    )).all()
    if not rows:
        return {"filings": []}
    dirx = await directory(viewer)
    level_map, _, _ = await levels(session, viewer, dirx)
    rows = [(filing, doc) for filing, doc in rows if has_level(level_map.get(doc.matter_id), "read")]
    paths = await _folder_paths(session, {doc.folder_id for _, doc in rows if doc.folder_id})
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for filing, doc in rows:
        key = (filing.message_id or filing.conversation_id, doc.id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            **_location(dirx, doc.matter_id, doc.folder_id, paths),
            "messageId": filing.message_id,
            "conversationId": filing.conversation_id,
            "sameMessage": bool(filing.message_id) and filing.message_id in ids,
            "kind": filing.kind,
            "documentId": doc.id,
            "documentName": doc.name,
            "version": doc.current_version,
            "filedBy": filing.filed_by_name,
            "filedAt": iso(filing.filed_at),
        })
    return {"filings": out}


@router.get("/mail/suggestions")
async def filing_suggestions(
    fromEmail: str = Query(default="", max_length=320),
    conversationId: str = Query(default="", max_length=300),
    limit: int = Query(default=8, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Ranked filing locations: this thread → this sender → sender's company → my recent locations."""
    sender = normalize_email(fromEmail)
    domain = email_domain(sender)
    conversation = conversationId.strip()

    buckets: list[tuple[str, object]] = []
    if conversation:
        buckets.append(("conversation", EmailFilingModel.conversation_id == conversation))
    if sender:
        buckets.append(("sender", EmailFilingModel.from_email == sender))
    if domain and domain not in _GENERIC_DOMAINS:
        buckets.append(("domain", EmailFilingModel.from_domain == domain))
    since = now() - timedelta(days=_RECENT_DAYS)
    buckets.append(("recent", (EmailFilingModel.filed_by_user_id == viewer.user_id) & (EmailFilingModel.filed_at >= since)))

    stats: dict[tuple[str, str | None], dict] = {}
    for reason, condition in buckets:
        rows = await session.execute(
            select(EmailFilingModel.matter_id, EmailFilingModel.folder_id, EmailFilingModel.filed_at)
            .where(condition)
            .order_by(EmailFilingModel.filed_at.desc())
            .limit(400)
        )
        for matter_id, folder_id, filed_at in rows.all():
            entry = stats.setdefault((matter_id, folder_id), {
                "reason": reason, "count": 0, "last": filed_at, "counts": {},
            })
            entry["counts"][reason] = entry["counts"].get(reason, 0) + 1
            if _REASON_RANK[reason] > _REASON_RANK[entry["reason"]]:
                entry["reason"] = reason
            if reason == entry["reason"]:
                entry["count"] = entry["counts"][reason]
            if filed_at and (entry["last"] is None or filed_at > entry["last"]):
                entry["last"] = filed_at
    if not stats:
        return {"locations": []}

    dirx = await directory(viewer)
    level_map, _, _ = await levels(session, viewer, dirx)
    paths = await _folder_paths(session, {fid for _, fid in stats if fid})
    ranked: list[dict] = []
    for (matter_id, folder_id), entry in stats.items():
        matter = dirx.matters.get(matter_id)
        if matter is None or matter_closed(dirx, matter) or not has_level(level_map.get(matter_id), "edit"):
            continue
        if folder_id and folder_id not in paths:
            continue
        ranked.append({
            **_location(dirx, matter_id, folder_id, paths),
            "reason": entry["reason"],
            "count": entry["count"],
            "lastFiledAt": iso(entry["last"]),
            "_rank": (_REASON_RANK[entry["reason"]], entry["count"], entry["last"] or since),
        })
    ranked.sort(key=lambda row: row["_rank"], reverse=True)
    for row in ranked:
        row.pop("_rank")
    return {"locations": ranked[:limit]}
