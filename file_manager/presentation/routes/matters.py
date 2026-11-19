from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import has_level, safe_name
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import DocumentModel, FolderModel, MatterWorkspaceModel
from presentation.common import (
    audit,
    crumbs,
    directory,
    doc_out,
    ensure_not_locked,
    ensure_provisioned,
    favorite_ids,
    folder_in_matter,
    iso,
    levels,
    matter_closed,
    matter_folders,
    next_doc_number,
    now,
    require_matter,
)
from presentation.deps import Viewer, get_viewer
from presentation.routes.mail import FILING_KINDS, mail_target_folder, new_email_filing
from presentation.intake import file_into_matter
from presentation.versioning import backfill_fingerprints, digest, store_content

router = APIRouter(tags=["file-manager"])


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    parentId: str | None = None


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    parentId: str | None = None
    moveToRoot: bool = False


@router.get("/clients")
async def list_clients(
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Clients → Matters tree with the viewer's effective level per matter."""
    dirx = await directory(viewer)
    level_map, tt, acl = await levels(session, viewer, dirx)
    counts = dict((await session.execute(
        select(DocumentModel.matter_id, func.count(DocumentModel.id))
        .where(DocumentModel.deleted_at.is_(None))
        .group_by(DocumentModel.matter_id)
    )).all())
    provisioned = set(await session.scalars(select(MatterWorkspaceModel.matter_id)))
    favorites = await favorite_ids(session, viewer.user_id)
    clients: dict[str, dict] = {}
    requestable: list[dict] = []
    for matter in dirx.matters.values():
        client = dirx.clients.get(matter.client_id)
        if client is None:
            continue
        level = level_map.get(matter.id, "none")
        closed = matter_closed(dirx, matter)
        if level == "none":
            if acl.get(matter.id) != "deny" and not closed:
                requestable.append({
                    "matterId": matter.id,
                    "matterName": matter.name,
                    "matterCode": matter.code,
                    "clientName": client.name,
                })
            continue
        row = clients.setdefault(client.id, {
            "id": client.id,
            "name": client.name,
            "archived": client.archived,
            "favorite": ("client", client.id) in favorites,
            "matters": [],
        })
        row["matters"].append({
            "id": matter.id,
            "clientId": client.id,
            "name": matter.name,
            "code": matter.code,
            "closed": closed,
            "level": level,
            "mine": matter.id in tt or acl.get(matter.id) in {"read", "edit", "manage"},
            "provisioned": matter.id in provisioned,
            "documents": int(counts.get(matter.id, 0)),
            "favorite": ("matter", matter.id) in favorites,
        })
    # Managers see every client from time-tracking, including archived ones with no accessible matters.
    if viewer.manager:
        for client in dirx.clients.values():
            clients.setdefault(client.id, {
                "id": client.id,
                "name": client.name,
                "archived": client.archived,
                "favorite": ("client", client.id) in favorites,
                "matters": [],
            })
            clients[client.id]["archived"] = client.archived
    out = sorted(clients.values(), key=lambda c: (c["archived"], c["name"].lower()))
    for client in out:
        client["matters"].sort(key=lambda m: (m["closed"], m["name"].lower()))
    requestable.sort(key=lambda r: (r["clientName"].lower(), r["matterName"].lower()))
    closed_matters = sum(1 for matter in dirx.matters.values() if matter_closed(dirx, matter))
    return {
        "viewer": {"userId": viewer.user_id, "name": viewer.name, "canManage": viewer.manager},
        "clients": out,
        "requestable": requestable,
        "stats": {
            "clients": len(dirx.clients),
            "matters": len(dirx.matters),
            "archivedClients": sum(1 for client in dirx.clients.values() if client.archived),
            "closedMatters": closed_matters,
            "visibleClients": len(out),
            "visibleMatters": sum(len(client["matters"]) for client in out),
        },
    }


@router.get("/matters/{matter_id}/browse")
async def browse_matter(
    matter_id: str,
    folderId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    dirx, matter, level = await require_matter(session, viewer, matter_id, "read")
    await ensure_provisioned(session, viewer, matter)
    folder = await folder_in_matter(session, matter.id, folderId)
    folders = await matter_folders(session, matter.id)
    doc_query = select(DocumentModel).where(
        DocumentModel.matter_id == matter.id,
        DocumentModel.deleted_at.is_(None),
    )
    if folder is None:
        doc_query = doc_query.where(DocumentModel.folder_id.is_(None))
    else:
        doc_query = doc_query.where(DocumentModel.folder_id == folder.id)
    docs = list(await session.scalars(doc_query.order_by(DocumentModel.name.asc())))
    per_folder = dict((await session.execute(
        select(DocumentModel.folder_id, func.count(DocumentModel.id))
        .where(DocumentModel.matter_id == matter.id, DocumentModel.deleted_at.is_(None))
        .group_by(DocumentModel.folder_id)
    )).all())
    favorites = await favorite_ids(session, viewer.user_id)
    client = dirx.clients.get(matter.client_id)
    return {
        "matter": {
            "id": matter.id,
            "name": matter.name,
            "code": matter.code,
            "closed": matter_closed(dirx, matter),
            "level": level,
            "favorite": ("matter", matter.id) in favorites,
        },
        "client": {"id": matter.client_id, "name": client.name if client else ""},
        "folderId": folder.id if folder else None,
        "path": crumbs(folders, folder.id if folder else None),
        "folders": [
            {
                "id": item.id,
                "parentId": item.parent_id,
                "name": item.name,
                "documents": int(per_folder.get(item.id, 0)),
                "createdAt": iso(item.created_at),
                "favorite": ("folder", item.id) in favorites,
            }
            for item in folders
        ],
        "documents": [doc_out(doc, dirx, favorites) for doc in docs],
    }


async def _sibling_name_taken(session: AsyncSession, matter_id: str, parent_id: str | None, name: str, skip: str = "") -> bool:
    query = select(FolderModel.id).where(
        FolderModel.matter_id == matter_id,
        FolderModel.deleted_at.is_(None),
        func.lower(FolderModel.name) == name.lower(),
    )
    query = query.where(FolderModel.parent_id.is_(None) if parent_id is None else FolderModel.parent_id == parent_id)
    if skip:
        query = query.where(FolderModel.id != skip)
    return (await session.scalar(query.limit(1))) is not None


@router.post("/matters/{matter_id}/folders", status_code=201)
async def create_folder(
    matter_id: str,
    body: FolderIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    _, matter, _ = await require_matter(session, viewer, matter_id, "edit")
    name = safe_name(body.name, "")
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Укажите название папки")
    parent = await folder_in_matter(session, matter.id, body.parentId)
    parent_id = parent.id if parent else None
    if await _sibling_name_taken(session, matter.id, parent_id, name):
        raise HTTPException(status_code=409, detail="Папка с таким названием уже есть")
    folder = FolderModel(
        id=str(uuid4()),
        matter_id=matter.id,
        client_id=matter.client_id,
        parent_id=parent_id,
        name=name,
        created_by_user_id=viewer.user_id,
        created_at=now(),
    )
    session.add(folder)
    audit(session, viewer, "folder_create", matter=matter, kind="folder", target_id=folder.id, target_name=name)
    await session.commit()
    return {"id": folder.id, "parentId": parent_id, "name": name}


@router.patch("/folders/{folder_id}")
async def update_folder(
    folder_id: str,
    body: FolderPatch,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    folder = await session.get(FolderModel, folder_id)
    if folder is None or folder.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    _, matter, _ = await require_matter(session, viewer, folder.matter_id, "edit")
    parent_id = folder.parent_id
    if body.moveToRoot:
        parent_id = None
    elif body.parentId:
        target = await folder_in_matter(session, matter.id, body.parentId)
        folders = {item.id: item for item in await matter_folders(session, matter.id)}
        cursor = target.id if target else None
        while cursor:
            if cursor == folder.id:
                raise HTTPException(status_code=400, detail="Нельзя переместить папку внутрь самой себя")
            cursor = folders[cursor].parent_id if cursor in folders else None
        parent_id = target.id if target else None
    name = safe_name(body.name, "") if body.name is not None else folder.name
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название папки")
    if await _sibling_name_taken(session, matter.id, parent_id, name, skip=folder.id):
        raise HTTPException(status_code=409, detail="Папка с таким названием уже есть")
    if name != folder.name:
        audit(session, viewer, "folder_rename", matter=matter, kind="folder", target_id=folder.id,
              target_name=name, detail=f"{folder.name} → {name}")
    if parent_id != folder.parent_id:
        audit(session, viewer, "folder_move", matter=matter, kind="folder", target_id=folder.id, target_name=name)
    folder.name = name
    folder.parent_id = parent_id
    await session.commit()
    return {"id": folder.id, "parentId": folder.parent_id, "name": folder.name}


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    folder = await session.get(FolderModel, folder_id)
    if folder is None or folder.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    _, matter, _ = await require_matter(session, viewer, folder.matter_id, "edit")
    folders = await matter_folders(session, matter.id)
    children: dict[str | None, list[str]] = {}
    for item in folders:
        children.setdefault(item.parent_id, []).append(item.id)
    subtree: list[str] = []
    stack = [folder.id]
    while stack:
        current = stack.pop()
        subtree.append(current)
        stack.extend(children.get(current, []))
    docs = list(await session.scalars(
        select(DocumentModel).where(DocumentModel.folder_id.in_(subtree), DocumentModel.deleted_at.is_(None))
    ))
    for doc in docs:
        ensure_not_locked(doc, viewer)
    stamp = now()
    by_id = {item.id: item for item in folders}
    for fid in subtree:
        item = by_id.get(fid)
        if item is not None:
            item.deleted_at = stamp
            item.deleted_by_name = viewer.name
            item.trash_root_id = folder.id
    for doc in docs:
        doc.deleted_at = stamp
        doc.deleted_by_name = viewer.name
        doc.trash_root_id = folder.id
    audit(session, viewer, "folder_delete", matter=matter, kind="folder", target_id=folder.id,
          target_name=folder.name, detail=f"{len(docs)} docs")
    await session.commit()
    return {"ok": True}


@router.post("/matters/{matter_id}/documents", status_code=201)
async def upload_documents(
    matter_id: str,
    files: list[UploadFile] = File(...),
    folderId: str | None = Form(default=None),
    comment: str = Form(default=""),
    mailMessageId: str = Form(default="", max_length=500),
    mailConversationId: str = Form(default="", max_length=300),
    mailFrom: str = Form(default="", max_length=320),
    mailSubject: str = Form(default="", max_length=2000),
    mailKind: str = Form(default="", max_length=16),
    allowDuplicate: bool = Form(default=False),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Upload into a matter folder without piling up copies.

    Per file the result is one of:
    * ``created``   — a new document;
    * ``version``   — same name in the folder (or the same thread) with new content → next version;
    * ``unchanged`` — same name and identical content → nothing written;
    * ``exists``    — this letter / attachment is already filed in the matter → nothing written;
    * ``duplicate`` — identical content already lives in the matter under another name
      (skipped unless ``allowDuplicate``).

    `mail*` fields come from the Outlook add-in: they record where the message was filed.
    """
    dirx, matter, _ = await require_matter(session, viewer, matter_id, "edit")
    folder = await folder_in_matter(session, matter.id, folderId)
    is_mail = bool(mailMessageId.strip() or mailConversationId.strip())
    kind = mailKind if mailKind in FILING_KINDS else "email"
    if is_mail:
        folder = await mail_target_folder(session, viewer, matter, folder)
        if kind == "email":
            # Letters filed before fingerprints existed are matched by their Message-ID header.
            await backfill_fingerprints(session, matter.id, mail_only=True)
    folder_id = folder.id if folder else None
    limit = get_settings().max_file_bytes
    stamp = now()
    counter = {"next": await next_doc_number(session)}

    async def next_number() -> int:
        value = counter["next"]
        counter["next"] += 1
        return value

    results: list[tuple[str, DocumentModel]] = []
    for upload in files:
        content = await upload.read()
        if not content:
            continue
        if len(content) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Файл больше {limit // (1024 * 1024)} МБ — он будет загружен частями",
            )
        name = safe_name(upload.filename)
        sha = digest(content)

        async def store(data: bytes = content) -> None:
            await store_content(data)

        results.append(await file_into_matter(
            session, viewer, matter,
            folder_id=folder_id,
            name=name,
            content_type=(upload.content_type or "application/octet-stream")[:120],
            size=len(content),
            sha=sha,
            head=content[:65536],
            comment=comment,
            allow_duplicate=allowDuplicate,
            stamp=stamp,
            next_number=next_number,
            store=store,
            is_mail=is_mail,
            kind=kind,
            mail_message_id=mailMessageId,
            mail_conversation_id=mailConversationId,
        ))
    if not results:
        raise HTTPException(status_code=400, detail="Выберите файл")
    if is_mail:
        subject = " ".join(mailSubject.split())[:500]
        for status, doc in results:
            if status != "created":
                continue
            # The subject is what people look for — keep it as the document description.
            if subject and not (doc.description or "").strip():
                doc.description = subject
            session.add(new_email_filing(
                viewer,
                doc,
                message_id=mailMessageId,
                conversation_id=mailConversationId,
                from_email=mailFrom,
                subject=mailSubject,
                kind=kind,
                stamp=stamp,
            ))
    await session.commit()
    out = [(status, doc_out(doc, dirx)) for status, doc in results]
    return {
        "documents": [doc for _, doc in out],
        "results": [{"status": status, "document": doc} for status, doc in out],
    }


@router.get("/matters/{matter_id}/level")
async def my_matter_level(
    matter_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    _, matter, level = await require_matter(session, viewer, matter_id, "read")
    return {"matterId": matter.id, "level": level, "canEdit": has_level(level, "edit")}
