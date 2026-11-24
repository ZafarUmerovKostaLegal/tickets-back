"""Access to one document: who sees it, who may change it, who is shut out, and confidential mode.

A document entry wins over folder permissions. A confidential document is not opened by folder
permissions at all: only its author, partners and the people listed on the document see it.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import effective_grant, is_partner
from infrastructure.database import get_session
from infrastructure.models import AclModel, DocumentAclModel, DocumentModel, DocumentPolicyModel, FolderModel
from infrastructure.tt_directory import invalidate_access, project_assignees
from presentation.common import acl_level, audit, iso, matter_folder_levels, now, require_matter
from presentation.deps import Viewer, get_viewer

router = APIRouter(tags=["file-manager"])

DOC_LEVELS = frozenset({"read", "edit", "deny"})


class DocAclIn(BaseModel):
    userId: int = Field(ge=1)
    userName: str = Field(default="", max_length=200)
    level: str = Field(max_length=16)
    note: str = Field(default="", max_length=500)
    until: datetime | None = None
    suspendedUntil: datetime | None = None


class RestrictIn(BaseModel):
    restricted: bool


async def _document(session: AsyncSession, viewer: Viewer, document_id: str):
    """The document and its matter; managed by matter managers and by the document's author."""
    doc = await session.get(DocumentModel, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    dirx, matter, level = await require_matter(session, viewer, doc.matter_id, "read")
    if level != "manage" and doc.created_by_user_id != viewer.user_id:
        raise HTTPException(status_code=403, detail="Доступом к файлу управляют автор и партнёры")
    return doc, dirx, matter


def _entry_out(row: DocumentAclModel, stamp: datetime) -> dict:
    return {
        "level": row.level,
        "effective": effective_grant(row.level, row.until, row.suspended_until, stamp),
        "note": row.note,
        "until": iso(row.until),
        "suspendedUntil": iso(row.suspended_until),
        "grantedBy": row.granted_by_name,
        "grantedAt": iso(row.granted_at),
    }


@router.get("/documents/{document_id}/access")
async def document_access(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc, dirx, matter = await _document(session, viewer, document_id)
    stamp = now()
    policy = await session.get(DocumentPolicyModel, doc.id)
    restricted = bool(policy and policy.restricted)
    folder = await session.get(FolderModel, doc.folder_id) if doc.folder_id else None
    entries = {row.user_id: row for row in await session.scalars(
        select(DocumentAclModel).where(DocumentAclModel.document_id == doc.id)
    )}

    members: dict[int, dict] = {}
    closed = dirx.matters[matter.id].archived
    for member in await project_assignees(viewer.authorization, matter.id):
        members[member["userId"]] = {
            "userId": member["userId"], "name": member["name"], "position": member.get("position") or "",
            "matterLevel": "read" if closed else "edit",
        }
    for acl in await session.scalars(select(AclModel).where(AclModel.matter_id == matter.id)):
        entry = members.setdefault(acl.user_id, {
            "userId": acl.user_id, "name": acl.user_name or f"#{acl.user_id}", "position": "",
        })
        entry["matterLevel"] = acl_level(acl) or "none"
    for user_id, row in entries.items():
        members.setdefault(user_id, {
            "userId": user_id, "name": row.user_name or f"#{user_id}", "position": "", "matterLevel": "none",
        })
    members.setdefault(doc.created_by_user_id, {
        "userId": doc.created_by_user_id, "name": doc.created_by_name or f"#{doc.created_by_user_id}",
        "position": "", "matterLevel": "edit",
    })

    out = []
    for user_id, member in members.items():
        partner = is_partner(None, member.get("position"))
        owner = user_id == doc.created_by_user_id
        via_folder = None
        if doc.folder_id:
            via_folder = (await matter_folder_levels(session, {matter.id}, user_id)).get(doc.folder_id)
        row = entries.get(user_id)
        mine = _entry_out(row, stamp) if row else None
        if partner:
            effective = "edit"
        elif owner:
            effective = "owner"
        elif via_folder == "deny":
            effective = "deny"
        elif mine and mine["effective"]:
            effective = mine["effective"]
        elif member.get("matterLevel") in (None, "none", "deny"):
            effective = "none"
        elif restricted or via_folder not in ("read", "edit"):
            effective = "own"
        else:
            effective = via_folder
        out.append({
            **member,
            "matterLevel": member.get("matterLevel", "none"),
            "partner": partner,
            "owner": owner,
            "entry": mine,
            "viaFolder": via_folder,
            "effective": effective,
        })
    out.sort(key=lambda item: (not item["owner"], item["entry"] is None, item["name"].lower()))
    return {
        "document": {
            "id": doc.id,
            "name": doc.name,
            "folderId": doc.folder_id,
            "folderName": folder.name if folder else "",
            "createdBy": doc.created_by_name,
            "createdById": doc.created_by_user_id,
            "restricted": restricted,
        },
        "matter": {"id": matter.id, "name": matter.name, "code": matter.code},
        "members": out,
    }


@router.put("/documents/{document_id}/access")
async def set_document_access(
    document_id: str,
    body: DocAclIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc, _, matter = await _document(session, viewer, document_id)
    if body.level not in DOC_LEVELS:
        raise HTTPException(status_code=400, detail="Неизвестный уровень доступа")
    if body.userId == doc.created_by_user_id:
        raise HTTPException(status_code=400, detail="Автор всегда видит свой файл")
    if body.until is not None and body.suspendedUntil is not None and body.suspendedUntil >= body.until:
        raise HTTPException(status_code=400, detail="Отключение должно закончиться раньше, чем сам доступ")
    row = await session.get(DocumentAclModel, (doc.id, body.userId))
    if row is None:
        row = DocumentAclModel(document_id=doc.id, user_id=body.userId, matter_id=matter.id,
                               level=body.level, granted_at=now())
        session.add(row)
    row.level = body.level
    row.user_name = body.userName.strip()[:200] or row.user_name
    row.note = body.note.strip()[:500]
    row.until = body.until
    row.suspended_until = body.suspendedUntil
    row.granted_by_name = viewer.name
    row.granted_at = now()
    detail = body.level
    if body.until:
        detail += f" until {body.until.isoformat()}"
    if body.suspendedUntil:
        detail += f" suspended until {body.suspendedUntil.isoformat()}"
    audit(session, viewer, "doc_acl_set", matter=matter, kind="document", target_id=doc.id,
          target_name=f"{doc.name} · {row.user_name}", detail=detail)
    await session.commit()
    invalidate_access(body.userId)
    return {"entry": _entry_out(row, now())}


@router.delete("/documents/{document_id}/access/{user_id}")
async def remove_document_access(
    document_id: str,
    user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc, _, matter = await _document(session, viewer, document_id)
    row = await session.get(DocumentAclModel, (doc.id, user_id))
    if row is not None:
        audit(session, viewer, "doc_acl_remove", matter=matter, kind="document", target_id=doc.id,
              target_name=f"{doc.name} · {row.user_name}", detail=row.level)
        await session.delete(row)
        await session.commit()
        invalidate_access(user_id)
    return {"ok": True}


@router.put("/documents/{document_id}/restricted")
async def set_document_restricted(
    document_id: str,
    body: RestrictIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc, _, matter = await _document(session, viewer, document_id)
    row = await session.get(DocumentPolicyModel, doc.id)
    if row is None:
        row = DocumentPolicyModel(document_id=doc.id, matter_id=matter.id, restricted=False, updated_at=now())
        session.add(row)
    row.restricted = body.restricted
    row.updated_by_name = viewer.name
    row.updated_at = now()
    audit(session, viewer, "doc_restrict", matter=matter, kind="document", target_id=doc.id,
          target_name=doc.name, detail="on" if body.restricted else "off")
    await session.commit()
    return {"restricted": row.restricted}
