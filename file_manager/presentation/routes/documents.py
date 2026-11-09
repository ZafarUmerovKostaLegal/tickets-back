from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import has_level, safe_name
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import AuditModel, DocumentModel, VersionModel
from presentation.common import (
    audit,
    crumbs,
    doc_out,
    ensure_not_locked,
    favorite_ids,
    folder_in_matter,
    iso,
    live_document,
    matter_folders,
    now,
    require_matter,
)
from presentation.deps import Viewer, get_viewer

router = APIRouter(tags=["file-manager"])

_OPEN_DEDUPE = timedelta(minutes=10)


class DocumentPatch(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    docType: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=4000)
    folderId: str | None = None
    moveToRoot: bool = False


def _version_out(row: VersionModel) -> dict:
    return {
        "version": row.version,
        "name": row.name,
        "sizeBytes": row.size_bytes,
        "comment": row.comment,
        "createdBy": row.created_by_name,
        "createdAt": iso(row.created_at),
    }


def _audit_out(row: AuditModel) -> dict:
    return {
        "id": row.id,
        "at": iso(row.at),
        "userId": row.user_id,
        "userName": row.user_name,
        "action": row.action,
        "matterId": row.matter_id,
        "matterName": row.matter_name,
        "targetKind": row.target_kind,
        "targetId": row.target_id,
        "targetName": row.target_name,
        "detail": row.detail,
    }


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc = await live_document(session, document_id)
    dirx, matter, level = await require_matter(session, viewer, doc.matter_id, "read")
    versions = list(await session.scalars(
        select(VersionModel).where(VersionModel.document_id == doc.id).order_by(VersionModel.version.desc())
    ))
    activity = list(await session.scalars(
        select(AuditModel)
        .where(AuditModel.target_id == doc.id)
        .order_by(AuditModel.at.desc())
        .limit(60)
    ))
    folders = await matter_folders(session, matter.id)
    favorites = await favorite_ids(session, viewer.user_id)
    return {
        "document": doc_out(doc, dirx, favorites),
        "level": level,
        "path": crumbs(folders, doc.folder_id),
        "versions": [_version_out(row) for row in versions],
        "activity": [_audit_out(row) for row in activity],
    }


@router.get("/documents/{document_id}/content")
async def document_content(
    document_id: str,
    version: int | None = Query(default=None, ge=1),
    inline: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc = await live_document(session, document_id)
    _, matter, _ = await require_matter(session, viewer, doc.matter_id, "read")
    wanted = version or doc.current_version
    row = await session.scalar(
        select(VersionModel).where(VersionModel.document_id == doc.id, VersionModel.version == wanted).limit(1)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    if inline:
        recent = await session.scalar(
            select(AuditModel.id).where(
                AuditModel.user_id == viewer.user_id,
                AuditModel.target_id == doc.id,
                AuditModel.action == "open",
                AuditModel.at >= now() - _OPEN_DEDUPE,
            ).limit(1)
        )
        if recent is None:
            audit(session, viewer, "open", matter=matter, kind="document", target_id=doc.id, target_name=doc.name)
            await session.commit()
    else:
        audit(session, viewer, "download", matter=matter, kind="document", target_id=doc.id,
              target_name=doc.name, detail=f"v{wanted}")
        await session.commit()
    disposition = "inline" if inline else "attachment"
    return Response(
        content=row.content,
        media_type=row.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(doc.name)}",
            "Cache-Control": "private, no-store",
        },
    )


@router.patch("/documents/{document_id}")
async def update_document(
    document_id: str,
    body: DocumentPatch,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc = await live_document(session, document_id)
    dirx, matter, _ = await require_matter(session, viewer, doc.matter_id, "edit")
    ensure_not_locked(doc, viewer)
    if body.name is not None:
        name = safe_name(body.name, "")
        if not name:
            raise HTTPException(status_code=400, detail="Укажите название документа")
        if name != doc.name:
            audit(session, viewer, "rename", matter=matter, kind="document", target_id=doc.id,
                  target_name=name, detail=f"{doc.name} → {name}")
            doc.name = name
    if body.docType is not None or body.description is not None:
        if body.docType is not None:
            doc.doc_type = body.docType.strip()[:60]
        if body.description is not None:
            doc.description = body.description.strip()[:4000]
        audit(session, viewer, "properties", matter=matter, kind="document", target_id=doc.id, target_name=doc.name)
    if body.moveToRoot or body.folderId:
        target = None if body.moveToRoot else await folder_in_matter(session, matter.id, body.folderId)
        target_id = target.id if target else None
        if target_id != doc.folder_id:
            doc.folder_id = target_id
            audit(session, viewer, "move", matter=matter, kind="document", target_id=doc.id,
                  target_name=doc.name, detail=target.name if target else "/")
    doc.updated_at = now()
    doc.updated_by_name = viewer.name
    await session.commit()
    return {"document": doc_out(doc, dirx)}


@router.post("/documents/{document_id}/checkout")
async def checkout_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc = await live_document(session, document_id)
    dirx, matter, _ = await require_matter(session, viewer, doc.matter_id, "edit")
    if doc.checked_out_by_user_id not in (None, viewer.user_id):
        raise HTTPException(status_code=409, detail=f"Документ уже взят: {doc.checked_out_by_name}")
    doc.checked_out_by_user_id = viewer.user_id
    doc.checked_out_by_name = viewer.name
    doc.checked_out_at = now()
    audit(session, viewer, "checkout", matter=matter, kind="document", target_id=doc.id, target_name=doc.name)
    await session.commit()
    return {"document": doc_out(doc, dirx)}


def _release(doc: DocumentModel) -> None:
    doc.checked_out_by_user_id = None
    doc.checked_out_by_name = ""
    doc.checked_out_at = None


@router.post("/documents/{document_id}/checkin")
async def checkin_document(
    document_id: str,
    file: UploadFile | None = File(default=None),
    comment: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc = await live_document(session, document_id)
    dirx, matter, _ = await require_matter(session, viewer, doc.matter_id, "edit")
    if doc.checked_out_by_user_id is None:
        raise HTTPException(status_code=400, detail="Документ не взят на редактирование")
    if doc.checked_out_by_user_id != viewer.user_id and not viewer.manager:
        raise HTTPException(status_code=409, detail=f"Документ взят: {doc.checked_out_by_name}")
    stamp = now()
    detail = "без изменений"
    if file is not None:
        content = await file.read()
        limit = get_settings().max_file_bytes
        if len(content) > limit:
            raise HTTPException(status_code=400, detail=f"Файл больше {limit // (1024 * 1024)} МБ")
        if content:
            doc.current_version += 1
            doc.size_bytes = len(content)
            doc.content_type = (file.content_type or doc.content_type)[:120]
            session.add(VersionModel(
                id=str(uuid4()),
                document_id=doc.id,
                version=doc.current_version,
                name=safe_name(file.filename, doc.name),
                content_type=doc.content_type,
                size_bytes=len(content),
                content=content,
                comment=(comment or "").strip()[:500],
                created_by_user_id=viewer.user_id,
                created_by_name=viewer.name,
                created_at=stamp,
            ))
            detail = f"v{doc.current_version}"
    _release(doc)
    doc.updated_at = stamp
    doc.updated_by_name = viewer.name
    audit(session, viewer, "checkin", matter=matter, kind="document", target_id=doc.id,
          target_name=doc.name, detail=detail)
    await session.commit()
    return {"document": doc_out(doc, dirx)}


@router.post("/documents/{document_id}/discard-checkout")
async def discard_checkout(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc = await live_document(session, document_id)
    dirx, matter, _ = await require_matter(session, viewer, doc.matter_id, "edit")
    if doc.checked_out_by_user_id is None:
        return {"document": doc_out(doc, dirx)}
    if doc.checked_out_by_user_id != viewer.user_id and not viewer.manager:
        raise HTTPException(status_code=409, detail=f"Документ взят: {doc.checked_out_by_name}")
    holder = doc.checked_out_by_name
    _release(doc)
    audit(session, viewer, "discard", matter=matter, kind="document", target_id=doc.id,
          target_name=doc.name, detail=holder)
    await session.commit()
    return {"document": doc_out(doc, dirx)}


@router.post("/documents/{document_id}/versions/{version}/restore")
async def restore_version(
    document_id: str,
    version: int,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc = await live_document(session, document_id)
    dirx, matter, _ = await require_matter(session, viewer, doc.matter_id, "edit")
    ensure_not_locked(doc, viewer)
    source = await session.scalar(
        select(VersionModel).where(VersionModel.document_id == doc.id, VersionModel.version == version).limit(1)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    if version == doc.current_version:
        return {"document": doc_out(doc, dirx)}
    stamp = now()
    doc.current_version = int(await session.scalar(
        select(func.max(VersionModel.version)).where(VersionModel.document_id == doc.id)
    ) or doc.current_version) + 1
    doc.size_bytes = source.size_bytes
    doc.content_type = source.content_type
    doc.updated_at = stamp
    doc.updated_by_name = viewer.name
    session.add(VersionModel(
        id=str(uuid4()),
        document_id=doc.id,
        version=doc.current_version,
        name=source.name,
        content_type=source.content_type,
        size_bytes=source.size_bytes,
        content=source.content,
        comment=f"Восстановлено из v{version}",
        created_by_user_id=viewer.user_id,
        created_by_name=viewer.name,
        created_at=stamp,
    ))
    audit(session, viewer, "version", matter=matter, kind="document", target_id=doc.id,
          target_name=doc.name, detail=f"v{version} → v{doc.current_version}")
    await session.commit()
    return {"document": doc_out(doc, dirx)}


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    doc = await live_document(session, document_id)
    _, matter, level = await require_matter(session, viewer, doc.matter_id, "edit")
    ensure_not_locked(doc, viewer)
    doc.deleted_at = now()
    doc.deleted_by_name = viewer.name
    doc.trash_root_id = doc.id
    audit(session, viewer, "delete", matter=matter, kind="document", target_id=doc.id, target_name=doc.name)
    await session.commit()
    return {"ok": True, "canPurge": has_level(level, "manage")}
