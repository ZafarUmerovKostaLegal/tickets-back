"""Folder permissions inside a matter: who sees all files of a folder, who may change them, who is shut out.

Entries flow down to subfolders until a folder stops inheriting. An entry may be temporary (``until``)
or suspended for a while (``suspendedUntil``) without losing it.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import effective_grant, is_partner
from infrastructure.database import get_session
from infrastructure.models import AclModel, FolderAclModel, FolderModel, FolderPolicyModel
from infrastructure.tt_directory import invalidate_access, project_assignees
from presentation.common import acl_level, audit, iso, matter_folders, now, require_matter
from presentation.deps import Viewer, get_viewer

router = APIRouter(tags=["file-manager"])

FOLDER_LEVELS = frozenset({"own", "read", "edit", "deny"})


class FolderAclIn(BaseModel):
    userId: int = Field(ge=1)
    userName: str = Field(default="", max_length=200)
    level: str = Field(max_length=16)
    note: str = Field(default="", max_length=500)
    until: datetime | None = None
    suspendedUntil: datetime | None = None


class InheritIn(BaseModel):
    inherit: bool


async def _folder(session: AsyncSession, viewer: Viewer, folder_id: str):
    folder = await session.get(FolderModel, folder_id)
    if folder is None or folder.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    dirx, matter, _ = await require_matter(session, viewer, folder.matter_id, "manage")
    return folder, dirx, matter


def _entry_out(row: FolderAclModel, stamp: datetime) -> dict:
    return {
        "level": row.level,
        "effective": effective_grant(row.level, row.until, row.suspended_until, stamp),
        "note": row.note,
        "until": iso(row.until),
        "suspendedUntil": iso(row.suspended_until),
        "grantedBy": row.granted_by_name,
        "grantedAt": iso(row.granted_at),
    }


def _inherited(
    folder: FolderModel,
    by_id: dict[str, FolderModel],
    entries: dict[str, str],
    breaks: set[str],
) -> tuple[str, str] | None:
    """Level that reaches this folder from its parents, and the folder it comes from."""
    if folder.id in breaks:
        return None
    found: tuple[str, str] | None = None
    current = folder.parent_id
    seen: set[str] = set()
    while current and current not in seen and current in by_id:
        seen.add(current)
        level = entries.get(current)
        if level == "deny":
            return "deny", current
        if level and found is None:
            found = (level, current)
        if current in breaks:
            break
        current = by_id[current].parent_id
    return found


@router.get("/folders/{folder_id}/access")
async def folder_access(
    folder_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    folder, dirx, matter = await _folder(session, viewer, folder_id)
    folders = await matter_folders(session, matter.id)
    by_id = {item.id: item for item in folders}
    stamp = now()
    rows = list(await session.scalars(select(FolderAclModel).where(FolderAclModel.matter_id == matter.id)))
    breaks = set(await session.scalars(select(FolderPolicyModel.folder_id).where(
        FolderPolicyModel.matter_id == matter.id, FolderPolicyModel.inherit.is_(False),
    )))
    per_user: dict[int, dict[str, str]] = {}
    own: dict[int, FolderAclModel] = {}
    names: dict[int, str] = {}
    for row in rows:
        names.setdefault(row.user_id, row.user_name)
        level = effective_grant(row.level, row.until, row.suspended_until, stamp)
        if row.folder_id == folder.id:
            own[row.user_id] = row
        elif level:
            per_user.setdefault(row.user_id, {})[row.folder_id] = level

    members: dict[int, dict] = {}
    closed = dirx.matters[matter.id].archived
    for member in await project_assignees(viewer.authorization, matter.id):
        members[member["userId"]] = {
            "userId": member["userId"],
            "name": member["name"],
            "position": member.get("position") or "",
            "matterLevel": "read" if closed else "edit",
            "source": "timeTracking",
        }
    for acl in await session.scalars(select(AclModel).where(AclModel.matter_id == matter.id)):
        level = acl_level(acl) or "none"
        entry = members.setdefault(acl.user_id, {
            "userId": acl.user_id, "name": acl.user_name or f"#{acl.user_id}", "position": "", "source": "acl",
        })
        entry["matterLevel"] = level
        entry["source"] = "both" if entry["source"] == "timeTracking" else "acl"
    for user_id in {*own, *per_user}:
        members.setdefault(user_id, {
            "userId": user_id, "name": names.get(user_id) or f"#{user_id}", "position": "",
            "matterLevel": "none", "source": "folder",
        })

    out = []
    for user_id, member in members.items():
        inherited = _inherited(folder, by_id, per_user.get(user_id, {}), breaks)
        row = own.get(user_id)
        mine = _entry_out(row, stamp) if row else None
        effective = (mine or {}).get("effective") or (inherited[0] if inherited else None)
        if inherited and inherited[0] == "deny":
            effective = "deny"
        out.append({
            **member,
            "partner": is_partner(None, member.get("position")),
            "entry": mine,
            "inherited": {"level": inherited[0], "folderId": inherited[1], "folderName": by_id[inherited[1]].name}
            if inherited else None,
            "effective": effective,
        })
    out.sort(key=lambda item: (item["entry"] is None and item["inherited"] is None, item["name"].lower()))
    path: list[dict] = []
    current = folder.parent_id
    while current and current in by_id and len(path) < 10:
        path.insert(0, {"id": current, "name": by_id[current].name})
        current = by_id[current].parent_id
    return {
        "folder": {
            "id": folder.id,
            "name": folder.name,
            "parentId": folder.parent_id,
            "inherit": folder.id not in breaks,
            "path": path,
        },
        "matter": {"id": matter.id, "name": matter.name, "code": matter.code},
        "members": out,
    }


@router.put("/folders/{folder_id}/access")
async def set_folder_access(
    folder_id: str,
    body: FolderAclIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    folder, _, matter = await _folder(session, viewer, folder_id)
    if body.level not in FOLDER_LEVELS:
        raise HTTPException(status_code=400, detail="Неизвестный уровень доступа")
    if body.until is not None and body.suspendedUntil is not None and body.suspendedUntil >= body.until:
        raise HTTPException(status_code=400, detail="Отключение должно закончиться раньше, чем сам доступ")
    row = await session.get(FolderAclModel, (folder.id, body.userId))
    if row is None:
        row = FolderAclModel(folder_id=folder.id, user_id=body.userId, matter_id=matter.id,
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
    audit(session, viewer, "folder_acl_set", matter=matter, kind="folder", target_id=folder.id,
          target_name=f"{folder.name} · {row.user_name}", detail=detail)
    await session.commit()
    invalidate_access(body.userId)
    return {"entry": _entry_out(row, now())}


@router.delete("/folders/{folder_id}/access/{user_id}")
async def remove_folder_access(
    folder_id: str,
    user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    folder, _, matter = await _folder(session, viewer, folder_id)
    row = await session.get(FolderAclModel, (folder.id, user_id))
    if row is not None:
        audit(session, viewer, "folder_acl_remove", matter=matter, kind="folder", target_id=folder.id,
              target_name=f"{folder.name} · {row.user_name}", detail=row.level)
        await session.delete(row)
        await session.commit()
        invalidate_access(user_id)
    return {"ok": True}


@router.put("/folders/{folder_id}/inherit")
async def set_folder_inherit(
    folder_id: str,
    body: InheritIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    folder, _, matter = await _folder(session, viewer, folder_id)
    row = await session.get(FolderPolicyModel, folder.id)
    if row is None:
        row = FolderPolicyModel(folder_id=folder.id, matter_id=matter.id, inherit=True, updated_at=now())
        session.add(row)
    row.inherit = body.inherit
    row.updated_by_name = viewer.name
    row.updated_at = now()
    audit(session, viewer, "folder_inherit", matter=matter, kind="folder", target_id=folder.id,
          target_name=folder.name, detail="on" if body.inherit else "off")
    await session.commit()
    return {"inherit": row.inherit}
