from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import (
    DEFAULT_TEMPLATE,
    file_kind,
    has_level,
    normalize_template,
    resolve_matter_level,
)
from infrastructure.models import (
    AclModel,
    AuditModel,
    DocumentModel,
    FavoriteModel,
    FolderModel,
    MatterWorkspaceModel,
    SettingModel,
)
from infrastructure.tt_directory import Directory, DirectoryUnavailable, Matter, load_directory, my_project_ids
from presentation.deps import Viewer

TEMPLATE_KEY = "matter_template"


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def doc_number(n: int) -> str:
    return f"KL-{n:06d}"


async def directory(viewer: Viewer, *, force: bool = False) -> Directory:
    try:
        return await load_directory(viewer.authorization, force=force)
    except DirectoryUnavailable as exc:
        raise HTTPException(status_code=503, detail="Сервис учёта времени недоступен (клиенты и matters)") from exc


def matter_closed(dirx: Directory, matter: Matter) -> bool:
    """Same rules as time-tracking: archived, paused, or end date in the past."""
    client = dirx.clients.get(matter.client_id)
    if matter.archived or bool(client and client.archived) or matter.paused:
        return True
    if matter.end_date and matter.end_date < date.today().isoformat():
        return True
    return False


def audit(
    session: AsyncSession,
    viewer: Viewer,
    action: str,
    *,
    matter: Matter | None = None,
    matter_id: str = "",
    kind: str = "",
    target_id: str = "",
    target_name: str = "",
    detail: str = "",
) -> None:
    session.add(AuditModel(
        id=str(uuid4()),
        at=now(),
        user_id=viewer.user_id,
        user_name=viewer.name,
        action=action,
        matter_id=(matter.id if matter else matter_id)[:80],
        matter_name=(matter.name if matter else "")[:300],
        target_kind=kind,
        target_id=target_id[:80],
        target_name=target_name[:300],
        detail=detail[:500],
    ))


async def acl_for_user(session: AsyncSession, user_id: int) -> dict[str, str]:
    rows = await session.scalars(select(AclModel).where(AclModel.user_id == user_id))
    return {row.matter_id: row.level for row in rows}


async def levels(session: AsyncSession, viewer: Viewer, dirx: Directory) -> tuple[dict[str, str], frozenset[str], dict[str, str]]:
    """Returns (level per matter, my time-tracking matters, explicit acl per matter)."""
    acl = await acl_for_user(session, viewer.user_id)
    tt = await my_project_ids(viewer.authorization, viewer.user_id)
    out = {
        mid: resolve_matter_level(
            manager=viewer.manager,
            super_admin=viewer.super_admin,
            tt_access=mid in tt,
            acl_level=acl.get(mid),
            matter_closed=matter_closed(dirx, matter),
        )
        for mid, matter in dirx.matters.items()
    }
    return out, tt, acl


async def matter_level(session: AsyncSession, viewer: Viewer, dirx: Directory, matter: Matter) -> str:
    acl = await session.get(AclModel, (matter.id, viewer.user_id))
    tt_access = False
    if not viewer.manager and (acl is None or acl.level == "deny"):
        tt_access = matter.id in await my_project_ids(viewer.authorization, viewer.user_id)
    return resolve_matter_level(
        manager=viewer.manager,
        super_admin=viewer.super_admin,
        tt_access=tt_access,
        acl_level=acl.level if acl else None,
        matter_closed=matter_closed(dirx, matter),
    )


async def require_matter(
    session: AsyncSession,
    viewer: Viewer,
    matter_id: str,
    need: str = "read",
) -> tuple[Directory, Matter, str]:
    dirx = await directory(viewer)
    matter = dirx.matters.get((matter_id or "").strip())
    if matter is None:
        dirx = await directory(viewer, force=True)
        matter = dirx.matters.get((matter_id or "").strip())
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter не найден")
    level = await matter_level(session, viewer, dirx, matter)
    if not has_level(level, need):
        audit(session, viewer, "denied", matter=matter, kind="matter", target_id=matter.id,
              target_name=matter.name, detail=f"need={need}")
        await session.commit()
        if level == "none":
            raise HTTPException(status_code=403, detail="Нет доступа к этому matter")
        raise HTTPException(status_code=403, detail="Недостаточно прав для этого действия")
    return dirx, matter, level


async def require_manager(viewer: Viewer) -> None:
    if not viewer.manager:
        raise HTTPException(status_code=403, detail="Доступно только администраторам и партнёрам")


async def template(session: AsyncSession) -> list[str]:
    row = await session.get(SettingModel, TEMPLATE_KEY)
    if row is None:
        return list(DEFAULT_TEMPLATE)
    value = row.value.get("folders") if isinstance(row.value, dict) else row.value
    return normalize_template(value)


async def ensure_provisioned(session: AsyncSession, viewer: Viewer, matter: Matter) -> None:
    """MacroView-style matter provisioning: first open creates the template folder tree."""
    if await session.get(MatterWorkspaceModel, matter.id) is not None:
        return
    paths = await template(session)
    created = now()
    ids: dict[str, str] = {}
    session.add(MatterWorkspaceModel(
        matter_id=matter.id,
        client_id=matter.client_id,
        provisioned_at=created,
        provisioned_by_user_id=viewer.user_id,
    ))
    for path in paths:
        parts = path.split("/")
        parent = ids.get("/".join(parts[:-1]).lower()) if len(parts) > 1 else None
        folder_id = str(uuid4())
        ids[path.lower()] = folder_id
        session.add(FolderModel(
            id=folder_id,
            matter_id=matter.id,
            client_id=matter.client_id,
            parent_id=parent,
            name=parts[-1],
            created_by_user_id=viewer.user_id,
            created_at=created,
        ))
    audit(session, viewer, "provision", matter=matter, kind="matter", target_id=matter.id,
          target_name=matter.name, detail=f"{len(paths)} folders")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()


async def matter_folders(session: AsyncSession, matter_id: str) -> list[FolderModel]:
    rows = await session.scalars(
        select(FolderModel)
        .where(FolderModel.matter_id == matter_id, FolderModel.deleted_at.is_(None))
        .order_by(FolderModel.name.asc())
    )
    return list(rows)


async def folder_in_matter(session: AsyncSession, matter_id: str, folder_id: str | None) -> FolderModel | None:
    fid = (folder_id or "").strip()
    if not fid:
        return None
    folder = await session.get(FolderModel, fid)
    if folder is None or folder.matter_id != matter_id or folder.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    return folder


def crumbs(folders: list[FolderModel], folder_id: str | None) -> list[dict]:
    by_id = {folder.id: folder for folder in folders}
    out: list[dict] = []
    current = folder_id
    seen: set[str] = set()
    while current and current in by_id and current not in seen:
        seen.add(current)
        folder = by_id[current]
        out.append({"id": folder.id, "name": folder.name})
        current = folder.parent_id
    out.reverse()
    return out


async def favorite_ids(session: AsyncSession, user_id: int) -> set[tuple[str, str]]:
    rows = await session.scalars(select(FavoriteModel).where(FavoriteModel.user_id == user_id))
    return {(row.kind, row.target_id) for row in rows}


async def next_doc_number(session: AsyncSession) -> int:
    current = await session.scalar(select(func.max(DocumentModel.doc_number)))
    return int(current or 0) + 1


def doc_out(doc: DocumentModel, dirx: Directory | None = None, favorites: set[tuple[str, str]] | None = None) -> dict:
    matter = dirx.matters.get(doc.matter_id) if dirx else None
    client = dirx.clients.get(doc.client_id) if dirx else None
    return {
        "id": doc.id,
        "docNumber": doc_number(doc.doc_number),
        "name": doc.name,
        "kind": file_kind(doc.name),
        "docType": doc.doc_type,
        "description": doc.description,
        "version": doc.current_version,
        "sizeBytes": doc.size_bytes,
        "contentType": doc.content_type,
        "folderId": doc.folder_id,
        "matterId": doc.matter_id,
        "matterName": matter.name if matter else "",
        "matterCode": matter.code if matter else "",
        "clientId": doc.client_id,
        "clientName": client.name if client else "",
        "createdAt": iso(doc.created_at),
        "createdBy": doc.created_by_name,
        "updatedAt": iso(doc.updated_at),
        "updatedBy": doc.updated_by_name,
        "checkedOutById": doc.checked_out_by_user_id,
        "checkedOutBy": doc.checked_out_by_name or None,
        "checkedOutAt": iso(doc.checked_out_at),
        "favorite": bool(favorites and ("document", doc.id) in favorites),
        "deletedAt": iso(doc.deleted_at),
    }


def ensure_not_locked(doc: DocumentModel, viewer: Viewer) -> None:
    if doc.checked_out_by_user_id is None or doc.checked_out_by_user_id == viewer.user_id or viewer.manager:
        return
    raise HTTPException(
        status_code=409,
        detail=f"Документ взят на редактирование: {doc.checked_out_by_name or 'другой сотрудник'}",
    )


async def live_document(session: AsyncSession, document_id: str) -> DocumentModel:
    doc = await session.get(DocumentModel, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc
