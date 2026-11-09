from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import file_kind, has_level
from infrastructure.database import get_session
from infrastructure.models import AuditModel, DocumentModel, FavoriteModel, FolderModel, VersionModel
from presentation.common import (
    audit,
    directory,
    doc_out,
    favorite_ids,
    iso,
    levels,
    now,
    require_matter,
)
from presentation.deps import Viewer, get_viewer

router = APIRouter(tags=["file-manager"])

_RECENT_ACTIONS = ("open", "download", "upload", "version", "checkout", "checkin", "rename", "properties")
_FAVORITE_KINDS = frozenset({"client", "matter", "folder", "document"})
_DOC_NUMBER = re.compile(r"^(?:kl-?)?0*(\d{1,9})$", re.IGNORECASE)


class FavoriteIn(BaseModel):
    kind: str = Field(max_length=16)
    targetId: str = Field(min_length=1, max_length=80)
    matterId: str = Field(default="", max_length=80)


async def _readable(session: AsyncSession, viewer: Viewer, need: str = "read"):
    dirx = await directory(viewer)
    level_map, _, _ = await levels(session, viewer, dirx)
    allowed = {mid for mid, level in level_map.items() if has_level(level, need)}
    return dirx, level_map, allowed


@router.get("/recent")
async def recent_documents(
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    dirx, _, allowed = await _readable(session, viewer)
    rows = await session.execute(
        select(AuditModel.target_id, func.max(AuditModel.at))
        .where(
            AuditModel.user_id == viewer.user_id,
            AuditModel.target_kind == "document",
            AuditModel.action.in_(_RECENT_ACTIONS),
        )
        .group_by(AuditModel.target_id)
        .order_by(func.max(AuditModel.at).desc())
        .limit(80)
    )
    order = [(target_id, at) for target_id, at in rows.all()]
    if not order:
        return {"documents": []}
    docs = {
        doc.id: doc
        for doc in await session.scalars(
            select(DocumentModel).where(DocumentModel.id.in_([item[0] for item in order]), DocumentModel.deleted_at.is_(None))
        )
    }
    favorites = await favorite_ids(session, viewer.user_id)
    out: list[dict] = []
    for target_id, at in order:
        doc = docs.get(target_id)
        if doc is None or doc.matter_id not in allowed:
            continue
        item = doc_out(doc, dirx, favorites)
        item["lastActivityAt"] = iso(at)
        out.append(item)
        if len(out) >= 40:
            break
    return {"documents": out}


@router.get("/checked-out")
async def checked_out_documents(
    scope: str = Query(default="mine"),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    dirx, _, allowed = await _readable(session, viewer)
    query = select(DocumentModel).where(
        DocumentModel.deleted_at.is_(None),
        DocumentModel.checked_out_by_user_id.is_not(None),
    )
    if scope != "all" or not viewer.manager:
        query = query.where(DocumentModel.checked_out_by_user_id == viewer.user_id)
    docs = list(await session.scalars(query.order_by(DocumentModel.checked_out_at.desc()).limit(300)))
    favorites = await favorite_ids(session, viewer.user_id)
    return {"documents": [doc_out(doc, dirx, favorites) for doc in docs if doc.matter_id in allowed]}


@router.get("/search")
async def search_documents(
    q: str = Query(default="", max_length=200),
    clientId: str = Query(default=""),
    matterId: str = Query(default=""),
    kind: str = Query(default=""),
    checkedOut: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    dirx, _, allowed = await _readable(session, viewer)
    if matterId:
        allowed = allowed & {matterId}
    if clientId:
        allowed = {mid for mid in allowed if dirx.matters[mid].client_id == clientId}
    if not allowed:
        return {"documents": [], "total": 0}
    text = q.strip()
    query = select(DocumentModel).where(DocumentModel.deleted_at.is_(None), DocumentModel.matter_id.in_(allowed))
    if text:
        like = f"%{text.lower()}%"
        terms = [func.lower(DocumentModel.name).like(like), func.lower(DocumentModel.description).like(like),
                 func.lower(DocumentModel.doc_type).like(like)]
        number = _DOC_NUMBER.match(text)
        if number:
            terms.append(DocumentModel.doc_number == int(number.group(1)))
        query = query.where(or_(*terms))
    if checkedOut:
        query = query.where(DocumentModel.checked_out_by_user_id.is_not(None))
    docs = list(await session.scalars(query.order_by(DocumentModel.updated_at.desc()).limit(500)))
    if kind:
        docs = [doc for doc in docs if file_kind(doc.name) == kind]
    favorites = await favorite_ids(session, viewer.user_id)
    return {"documents": [doc_out(doc, dirx, favorites) for doc in docs[:200]], "total": len(docs)}


@router.get("/favorites")
async def list_favorites(
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    dirx, level_map, allowed = await _readable(session, viewer)
    rows = list(await session.scalars(
        select(FavoriteModel).where(FavoriteModel.user_id == viewer.user_id).order_by(FavoriteModel.created_at.desc())
    ))
    favorites = {(row.kind, row.target_id) for row in rows}
    matters: list[dict] = []
    folder_ids: list[str] = []
    doc_ids: list[str] = []
    for row in rows:
        if row.kind == "matter" and row.target_id in allowed:
            matter = dirx.matters[row.target_id]
            client = dirx.clients.get(matter.client_id)
            matters.append({
                "id": matter.id,
                "name": matter.name,
                "code": matter.code,
                "clientId": matter.client_id,
                "clientName": client.name if client else "",
                "level": level_map.get(matter.id, "none"),
            })
        elif row.kind == "folder":
            folder_ids.append(row.target_id)
        elif row.kind == "document":
            doc_ids.append(row.target_id)
    folders: list[dict] = []
    if folder_ids:
        for folder in await session.scalars(
            select(FolderModel).where(FolderModel.id.in_(folder_ids), FolderModel.deleted_at.is_(None))
        ):
            if folder.matter_id not in allowed:
                continue
            matter = dirx.matters.get(folder.matter_id)
            folders.append({
                "id": folder.id,
                "name": folder.name,
                "matterId": folder.matter_id,
                "matterName": matter.name if matter else "",
            })
    documents: list[dict] = []
    if doc_ids:
        for doc in await session.scalars(
            select(DocumentModel).where(DocumentModel.id.in_(doc_ids), DocumentModel.deleted_at.is_(None))
        ):
            if doc.matter_id in allowed:
                documents.append(doc_out(doc, dirx, favorites))
    return {"matters": matters, "folders": folders, "documents": documents}


@router.put("/favorites")
async def add_favorite(
    body: FavoriteIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    if body.kind not in _FAVORITE_KINDS:
        raise HTTPException(status_code=400, detail="Неизвестный тип избранного")
    if body.matterId:
        await require_matter(session, viewer, body.matterId, "read")
    existing = await session.get(FavoriteModel, (viewer.user_id, body.kind, body.targetId))
    if existing is None:
        session.add(FavoriteModel(
            user_id=viewer.user_id,
            kind=body.kind,
            target_id=body.targetId,
            matter_id=body.matterId,
            created_at=now(),
        ))
        await session.commit()
    return {"ok": True}


@router.delete("/favorites/{kind}/{target_id}")
async def remove_favorite(
    kind: str,
    target_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    await session.execute(delete(FavoriteModel).where(
        FavoriteModel.user_id == viewer.user_id,
        FavoriteModel.kind == kind,
        FavoriteModel.target_id == target_id,
    ))
    await session.commit()
    return {"ok": True}


@router.get("/trash")
async def list_trash(
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    dirx, level_map, allowed = await _readable(session, viewer, "edit")
    if not allowed:
        return {"items": []}
    folders = list(await session.scalars(
        select(FolderModel).where(
            FolderModel.deleted_at.is_not(None),
            FolderModel.trash_root_id == FolderModel.id,
            FolderModel.matter_id.in_(allowed),
        )
    ))
    docs = list(await session.scalars(
        select(DocumentModel).where(
            DocumentModel.deleted_at.is_not(None),
            DocumentModel.trash_root_id == DocumentModel.id,
            DocumentModel.matter_id.in_(allowed),
        )
    ))
    nested = dict((await session.execute(
        select(DocumentModel.trash_root_id, func.count(DocumentModel.id))
        .where(DocumentModel.trash_root_id.in_([folder.id for folder in folders] or [""]))
        .group_by(DocumentModel.trash_root_id)
    )).all())

    def base(matter_id: str) -> dict:
        matter = dirx.matters.get(matter_id)
        client = dirx.clients.get(matter.client_id) if matter else None
        return {
            "matterId": matter_id,
            "matterName": matter.name if matter else "",
            "clientName": client.name if client else "",
            "canPurge": has_level(level_map.get(matter_id), "manage"),
        }

    items = [
        {**base(folder.matter_id), "kind": "folder", "id": folder.id, "name": folder.name,
         "deletedAt": iso(folder.deleted_at), "deletedBy": folder.deleted_by_name,
         "documents": int(nested.get(folder.id, 0))}
        for folder in folders
    ] + [
        {**base(doc.matter_id), "kind": "document", "id": doc.id, "name": doc.name,
         "deletedAt": iso(doc.deleted_at), "deletedBy": doc.deleted_by_name,
         "fileKind": file_kind(doc.name), "sizeBytes": doc.size_bytes}
        for doc in docs
    ]
    items.sort(key=lambda item: item["deletedAt"] or "", reverse=True)
    return {"items": items}


async def _trash_root(session: AsyncSession, kind: str, item_id: str) -> FolderModel | DocumentModel:
    model = FolderModel if kind == "folder" else DocumentModel if kind == "document" else None
    if model is None:
        raise HTTPException(status_code=400, detail="Неизвестный тип")
    row = await session.get(model, item_id)
    if row is None or row.deleted_at is None or row.trash_root_id != row.id:
        raise HTTPException(status_code=404, detail="Элемент корзины не найден")
    return row


@router.post("/trash/{kind}/{item_id}/restore")
async def restore_trash(
    kind: str,
    item_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    root = await _trash_root(session, kind, item_id)
    _, matter, _ = await require_matter(session, viewer, root.matter_id, "edit")
    folders = list(await session.scalars(select(FolderModel).where(FolderModel.trash_root_id == root.id)))
    docs = list(await session.scalars(select(DocumentModel).where(DocumentModel.trash_root_id == root.id)))
    for row in [*folders, *docs]:
        row.deleted_at = None
        row.deleted_by_name = ""
        row.trash_root_id = None
    parent_id = root.parent_id if isinstance(root, FolderModel) else root.folder_id
    if parent_id:
        parent = await session.get(FolderModel, parent_id)
        if parent is None or parent.deleted_at is not None:
            if isinstance(root, FolderModel):
                root.parent_id = None
            else:
                root.folder_id = None
    audit(session, viewer, "restore", matter=matter, kind=kind, target_id=root.id, target_name=root.name)
    await session.commit()
    return {"ok": True}


@router.delete("/trash/{kind}/{item_id}")
async def purge_trash(
    kind: str,
    item_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    root = await _trash_root(session, kind, item_id)
    _, matter, _ = await require_matter(session, viewer, root.matter_id, "manage")
    doc_ids = list(await session.scalars(select(DocumentModel.id).where(DocumentModel.trash_root_id == root.id)))
    if doc_ids:
        await session.execute(delete(VersionModel).where(VersionModel.document_id.in_(doc_ids)))
        await session.execute(delete(DocumentModel).where(DocumentModel.id.in_(doc_ids)))
        await session.execute(delete(FavoriteModel).where(FavoriteModel.target_id.in_(doc_ids)))
    await session.execute(delete(FolderModel).where(FolderModel.trash_root_id == root.id))
    audit(session, viewer, "purge", matter=matter, kind=kind, target_id=root.id, target_name=root.name,
          detail=f"{len(doc_ids)} docs")
    await session.commit()
    return {"ok": True}
