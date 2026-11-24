from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import (
    DEFAULT_TEMPLATE,
    effective_grant,
    file_kind,
    folder_levels,
    has_level,
    normalize_template,
    resolve_matter_level,
)
from infrastructure.models import (
    AclModel,
    AuditModel,
    DocumentAclModel,
    DocumentModel,
    DocumentPolicyModel,
    FavoriteModel,
    FolderAclModel,
    FolderModel,
    FolderPolicyModel,
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


def acl_level(row: AclModel | None) -> str | None:
    if row is None:
        return None
    return effective_grant(row.level, row.until, row.suspended_until, now())


async def acl_for_user(session: AsyncSession, user_id: int) -> dict[str, str]:
    rows = await session.scalars(select(AclModel).where(AclModel.user_id == user_id))
    out: dict[str, str] = {}
    for row in rows:
        level = acl_level(row)
        if level:
            out[row.matter_id] = level
    return out


async def folder_grant_matters(session: AsyncSession, user_id: int) -> set[str]:
    """Matters where the user holds a working (not closed) folder or document permission."""
    stamp = now()
    out: set[str] = set()
    for model in (FolderAclModel, DocumentAclModel):
        for row in await session.scalars(select(model).where(model.user_id == user_id)):
            if effective_grant(row.level, row.until, row.suspended_until, stamp) in ("read", "edit"):
                out.add(row.matter_id)
    return out


async def levels(session: AsyncSession, viewer: Viewer, dirx: Directory) -> tuple[dict[str, str], frozenset[str], dict[str, str]]:
    """Returns (level per matter, my time-tracking matters, explicit acl per matter)."""
    acl = await acl_for_user(session, viewer.user_id)
    tt = await my_project_ids(viewer.authorization, viewer.user_id)
    by_folder = set() if viewer.manager else await folder_grant_matters(session, viewer.user_id)
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
    for mid in by_folder:
        if out.get(mid) == "none" and acl.get(mid) != "deny" and mid in dirx.matters:
            out[mid] = "read"
    return out, tt, acl


async def base_matter_level(session: AsyncSession, viewer: Viewer, dirx: Directory, matter: Matter) -> tuple[str, str | None]:
    """Matter level from the matter ACL and time tracking only (without folder permissions)."""
    level_from_acl = acl_level(await session.get(AclModel, (matter.id, viewer.user_id)))
    tt_access = False
    if not viewer.manager and (level_from_acl is None or level_from_acl == "deny"):
        tt_access = matter.id in await my_project_ids(viewer.authorization, viewer.user_id)
    level = resolve_matter_level(
        manager=viewer.manager,
        super_admin=viewer.super_admin,
        tt_access=tt_access,
        acl_level=level_from_acl,
        matter_closed=matter_closed(dirx, matter),
    )
    return level, level_from_acl


async def matter_level(session: AsyncSession, viewer: Viewer, dirx: Directory, matter: Matter) -> str:
    level, from_acl = await base_matter_level(session, viewer, dirx, matter)
    if level == "none" and from_acl != "deny" and not viewer.manager:
        if matter.id in await folder_grant_matters(session, viewer.user_id):
            return "read"
    return level


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


def sees_all_documents(viewer: Viewer) -> bool:
    """Partners and admins see every document of a matter; other staff only the ones they uploaded
    plus the folders opened to them."""
    return viewer.manager


async def matter_folder_levels(session: AsyncSession, matter_ids: set[str], user_id: int) -> dict[str, str]:
    """Effective folder level (read / edit / deny) of one user in these matters, with inheritance."""
    if not matter_ids:
        return {}
    stamp = now()
    entries: dict[str, str] = {}
    for row in await session.scalars(select(FolderAclModel).where(
        FolderAclModel.user_id == user_id, FolderAclModel.matter_id.in_(matter_ids),
    )):
        level = effective_grant(row.level, row.until, row.suspended_until, stamp)
        if level:
            entries[row.folder_id] = level
    if not entries:
        return {}
    parents = dict((await session.execute(
        select(FolderModel.id, FolderModel.parent_id)
        .where(FolderModel.matter_id.in_(matter_ids), FolderModel.deleted_at.is_(None))
    )).all())
    breaks = set(await session.scalars(select(FolderPolicyModel.folder_id).where(
        FolderPolicyModel.matter_id.in_(matter_ids), FolderPolicyModel.inherit.is_(False),
    )))
    return folder_levels(parents, entries, breaks)


class AccessMap(dict):
    """Effective folder levels of one user (the dict itself) plus document levels in ``docs``."""

    def __init__(self, folders: dict[str, str] | None = None, docs: dict[str, str] | None = None) -> None:
        super().__init__(folders or {})
        self.docs: dict[str, str] = docs or {}


async def matter_doc_levels(session: AsyncSession, matter_ids: set[str] | None, user_id: int) -> dict[str, str]:
    query = select(DocumentAclModel).where(DocumentAclModel.user_id == user_id)
    if matter_ids is not None:
        if not matter_ids:
            return {}
        query = query.where(DocumentAclModel.matter_id.in_(matter_ids))
    stamp = now()
    out: dict[str, str] = {}
    for row in await session.scalars(query):
        level = effective_grant(row.level, row.until, row.suspended_until, stamp)
        if level:
            out[row.document_id] = level
    return out


async def my_folder_levels(session: AsyncSession, viewer: Viewer, matter_ids: set[str] | None = None) -> AccessMap:
    if sees_all_documents(viewer):
        return AccessMap()
    docs = await matter_doc_levels(session, matter_ids, viewer.user_id)
    if matter_ids is None:
        matter_ids = set(await session.scalars(
            select(FolderAclModel.matter_id).where(FolderAclModel.user_id == viewer.user_id).distinct()
        ))
    return AccessMap(await matter_folder_levels(session, matter_ids, viewer.user_id), docs)


def _restricted_ids():
    return select(DocumentPolicyModel.document_id).where(DocumentPolicyModel.restricted.is_(True))


def scope_clause(viewer: Viewer, folder_map: dict[str, str], need: str = "read") -> tuple:
    """
    Own documents, documents opened one by one, and documents of opened folders that are neither
    confidential nor covered by a document entry of their own. Closed folders hide everything inside.
    """
    if sees_all_documents(viewer):
        return ()
    wanted = ("read", "edit") if need == "read" else ("edit",)
    docs: dict[str, str] = getattr(folder_map, "docs", {})
    opened = [fid for fid, level in folder_map.items() if level in wanted]
    closed = [fid for fid, level in folder_map.items() if level == "deny"]
    granted = [did for did, level in docs.items() if level in wanted]
    parts: list = [DocumentModel.created_by_user_id == viewer.user_id]
    if granted:
        parts.append(DocumentModel.id.in_(granted))
    if opened:
        through_folder = [DocumentModel.folder_id.in_(opened), DocumentModel.id.not_in(_restricted_ids())]
        if docs:
            through_folder.append(DocumentModel.id.not_in(list(docs)))
        parts.append(and_(*through_folder))
    out: list = [or_(*parts) if len(parts) > 1 else parts[0]]
    if closed:
        out.append(or_(DocumentModel.folder_id.is_(None), DocumentModel.folder_id.not_in(closed)))
    return tuple(out)


async def is_restricted(session: AsyncSession, document_id: str) -> bool:
    row = await session.get(DocumentPolicyModel, document_id)
    return bool(row and row.restricted)


async def doc_scope(session: AsyncSession, viewer: Viewer, need: str = "read", matter_ids: set[str] | None = None) -> tuple:
    """SQL conditions on DocumentModel: what this viewer may see (``read``) or change (``edit``)."""
    if sees_all_documents(viewer):
        return ()
    return scope_clause(viewer, await my_folder_levels(session, viewer, matter_ids), need)


async def document_allowed(session: AsyncSession, viewer: Viewer, doc: DocumentModel, need: str = "read") -> bool:
    if sees_all_documents(viewer):
        return True
    access = await my_folder_levels(session, viewer, {doc.matter_id})
    level = access.get(doc.folder_id or "")
    if level == "deny":
        return False
    if doc.created_by_user_id == viewer.user_id:
        return True
    own = access.docs.get(doc.id)
    if own:
        return own == "edit" or (need == "read" and own == "read")
    if level not in ("read", "edit") or await is_restricted(session, doc.id):
        return False
    return level == "edit" or need == "read"


async def require_document_edit(session: AsyncSession, viewer: Viewer, doc: DocumentModel) -> None:
    if not await document_allowed(session, viewer, doc, "edit"):
        raise HTTPException(status_code=403, detail="Этот документ доступен вам только для просмотра")


async def ensure_folder_open(session: AsyncSession, viewer: Viewer, folder: FolderModel | None) -> None:
    if folder is None or sees_all_documents(viewer):
        return
    if (await my_folder_levels(session, viewer, {folder.matter_id})).get(folder.id) == "deny":
        raise HTTPException(status_code=403, detail="Доступ к этой папке закрыт")


async def live_document(session: AsyncSession, document_id: str, viewer: Viewer | None = None) -> DocumentModel:
    doc = await session.get(DocumentModel, document_id)
    if doc is None or doc.deleted_at is not None or (
        viewer is not None and not await document_allowed(session, viewer, doc)
    ):
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc
