from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.models import LegalHubDriveAclModel, LegalHubDriveFileModel, LegalHubDriveFolderModel
from presentation.deps import require_user

router = APIRouter(tags=["legal-hub"])
MAX_FILE_BYTES = 15 * 1024 * 1024
ROOT_ACL_ID = "__root__"
ACL_LEVELS = frozenset({"read", "edit", "manage", "deny"})
LEVEL_RANK = {"deny": 0, "read": 1, "edit": 2, "manage": 3}


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    parentId: str | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class AclIn(BaseModel):
    userId: int
    userName: str = Field(default="", max_length=200)
    level: str = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=500)

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        level = value.strip().lower()
        if level not in ACL_LEVELS:
            raise ValueError("Invalid ACL level")
        return level

    @field_validator("userName", "note")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


def _user_id(user: dict) -> int:
    return int(user["id"])


def _user_name(user: dict) -> str:
    return str(user.get("name") or user.get("display_name") or user.get("email") or "").strip() or f"User {_user_id(user)}"


def _is_manager(user: dict) -> bool:
    role = str(user.get("role") or "").strip().lower()
    return "администратор" in role or "partner" in role or "партнёр" in role or "партнер" in role


def _acl_folder_id(folder_id: str | None) -> str:
    return (folder_id or "").strip() or ROOT_ACL_ID


def _acl_out(row: LegalHubDriveAclModel) -> dict:
    return {
        "folderId": None if row.folder_id == ROOT_ACL_ID else row.folder_id,
        "userId": row.user_id,
        "userName": row.user_name,
        "level": row.level,
        "note": row.note,
        "grantedBy": row.granted_by_name,
        "grantedAt": row.granted_at.isoformat() if row.granted_at else None,
    }


def _file_kind(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "image"
    if lowered.endswith((".doc", ".docx")):
        return "doc"
    if lowered.endswith((".xls", ".xlsx", ".csv")):
        return "sheet"
    return "file"


def _file_size(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} KB"
    return f"{size / (1024 * 1024):.1f} MB"


async def _folder_or_404(session: AsyncSession, folder_id: str | None) -> LegalHubDriveFolderModel | None:
    if not folder_id:
        return None
    folder = await session.get(LegalHubDriveFolderModel, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    return folder


async def _crumbs(session: AsyncSession, folder_id: str | None) -> list[dict]:
    crumbs: list[dict] = []
    current = folder_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        folder = await session.get(LegalHubDriveFolderModel, current)
        if folder is None:
            break
        crumbs.append({"id": folder.id, "name": folder.name})
        current = folder.parent_id
    crumbs.reverse()
    return crumbs


async def _effective_level(session: AsyncSession, user: dict, folder_id: str | None) -> str:
    if _is_manager(user):
        return "manage"
    uid = _user_id(user)
    current = folder_id
    seen: set[str] = set()
    while True:
        key = _acl_folder_id(current)
        if key in seen:
            break
        seen.add(key)
        row = await session.get(LegalHubDriveAclModel, (key, uid))
        if row is not None:
            return row.level if row.level in LEVEL_RANK else "deny"
        if current is None:
            break
        folder = await session.get(LegalHubDriveFolderModel, current)
        if folder is None:
            break
        current = folder.parent_id
    # No ACL on the path → open for authenticated users (storage page, no UNC mount).
    return "edit"


async def _require_level(session: AsyncSession, user: dict, folder_id: str | None, need: str) -> str:
    level = await _effective_level(session, user, folder_id)
    if LEVEL_RANK.get(level, 0) < LEVEL_RANK.get(need, 99):
        raise HTTPException(status_code=403, detail="Недостаточно прав для этой папки")
    return level


@router.get("/drive")
async def list_drive(
    parentId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    parent = (parentId or "").strip() or None
    await _folder_or_404(session, parent)
    level = await _require_level(session, user, parent, "read")
    folder_query = select(LegalHubDriveFolderModel).order_by(LegalHubDriveFolderModel.name.asc())
    file_query = select(LegalHubDriveFileModel).order_by(LegalHubDriveFileModel.name.asc())
    if parent is None:
        folder_query = folder_query.where(LegalHubDriveFolderModel.parent_id.is_(None))
        file_query = file_query.where(LegalHubDriveFileModel.folder_id.is_(None))
    else:
        folder_query = folder_query.where(LegalHubDriveFolderModel.parent_id == parent)
        file_query = file_query.where(LegalHubDriveFileModel.folder_id == parent)
    folders = list(await session.scalars(folder_query))
    files = list(await session.scalars(file_query))
    return {
        "path": await _crumbs(session, parent),
        "folderId": parent,
        "level": level,
        "canEdit": LEVEL_RANK.get(level, 0) >= LEVEL_RANK["edit"],
        "canManage": LEVEL_RANK.get(level, 0) >= LEVEL_RANK["manage"],
        "folders": [
            {"id": item.id, "name": item.name, "createdAt": item.created_at.isoformat()}
            for item in folders
        ],
        "files": [
            {
                "id": item.id,
                "name": item.name,
                "kind": _file_kind(item.name),
                "size": _file_size(item.size_bytes),
                "sizeBytes": item.size_bytes,
                "createdAt": item.created_at.isoformat(),
            }
            for item in files
        ],
    }


@router.post("/drive/folders", status_code=201)
async def create_folder(
    body: FolderIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    if not body.name or body.name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Укажите название папки")
    parent = (body.parentId or "").strip() or None
    await _folder_or_404(session, parent)
    await _require_level(session, user, parent, "edit")
    session.add(LegalHubDriveFolderModel(
        id=str(uuid4()),
        parent_id=parent,
        name=body.name,
        created_by_user_id=_user_id(user),
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()
    return await list_drive(parent, session, user)


@router.post("/drive/files", status_code=201)
async def upload_files(
    files: list[UploadFile] = File(...),
    parentId: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    parent = (parentId or "").strip() or None
    await _folder_or_404(session, parent)
    await _require_level(session, user, parent, "edit")
    now = datetime.now(timezone.utc)
    stored = 0
    for upload in files:
        content = await upload.read()
        if not content:
            continue
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail="Файл больше 15 МБ")
        name = (upload.filename or "file").strip()[:300] or "file"
        session.add(LegalHubDriveFileModel(
            id=str(uuid4()),
            folder_id=parent,
            name=name,
            content_type=(upload.content_type or "application/octet-stream")[:120],
            size_bytes=len(content),
            content=content,
            created_by_user_id=_user_id(user),
            created_at=now,
        ))
        stored += 1
    if stored == 0:
        raise HTTPException(status_code=400, detail="Выберите файл")
    await session.commit()
    return await list_drive(parent, session, user)


@router.get("/drive/files/{file_id}")
async def download_drive_file(
    file_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    item = await session.get(LegalHubDriveFileModel, file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    await _require_level(session, user, item.folder_id, "read")
    return Response(
        content=item.content,
        media_type=item.content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(item.name)}"},
    )


@router.delete("/drive/files/{file_id}")
async def delete_drive_file(
    file_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    item = await session.get(LegalHubDriveFileModel, file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    parent = item.folder_id
    await _require_level(session, user, parent, "edit")
    await session.delete(item)
    await session.commit()
    return await list_drive(parent, session, user)


async def _delete_folder_tree(session: AsyncSession, folder_id: str) -> None:
    children = list(await session.scalars(
        select(LegalHubDriveFolderModel).where(LegalHubDriveFolderModel.parent_id == folder_id)
    ))
    for child in children:
        await _delete_folder_tree(session, child.id)
    files = list(await session.scalars(
        select(LegalHubDriveFileModel).where(LegalHubDriveFileModel.folder_id == folder_id)
    ))
    for item in files:
        await session.delete(item)
    acls = list(await session.scalars(
        select(LegalHubDriveAclModel).where(LegalHubDriveAclModel.folder_id == folder_id)
    ))
    for row in acls:
        await session.delete(row)
    folder = await session.get(LegalHubDriveFolderModel, folder_id)
    if folder is not None:
        await session.delete(folder)


@router.delete("/drive/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    folder = await _folder_or_404(session, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    parent = folder.parent_id
    await _require_level(session, user, folder.id, "edit")
    await _delete_folder_tree(session, folder.id)
    await session.commit()
    return await list_drive(parent, session, user)


@router.get("/drive/security")
async def get_drive_security(
    folderId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    folder = (folderId or "").strip() or None
    if folder:
        await _folder_or_404(session, folder)
    await _require_level(session, user, folder, "manage")
    key = _acl_folder_id(folder)
    rows = list(await session.scalars(
        select(LegalHubDriveAclModel)
        .where(LegalHubDriveAclModel.folder_id == key)
        .order_by(LegalHubDriveAclModel.user_name.asc())
    ))
    return {
        "folderId": folder,
        "acl": [_acl_out(row) for row in rows],
    }


@router.put("/drive/security")
async def set_drive_acl(
    body: AclIn,
    folderId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    folder = (folderId or "").strip() or None
    if folder:
        await _folder_or_404(session, folder)
    await _require_level(session, user, folder, "manage")
    key = _acl_folder_id(folder)
    now = datetime.now(timezone.utc)
    row = await session.get(LegalHubDriveAclModel, (key, body.userId))
    if row is None:
        row = LegalHubDriveAclModel(
            folder_id=key,
            user_id=body.userId,
            user_name=body.userName or f"User {body.userId}",
            level=body.level,
            note=body.note,
            granted_by_name=_user_name(user),
            granted_at=now,
        )
        session.add(row)
    else:
        row.user_name = body.userName or row.user_name
        row.level = body.level
        row.note = body.note
        row.granted_by_name = _user_name(user)
        row.granted_at = now
    await session.commit()
    return {"acl": _acl_out(row)}


@router.delete("/drive/security/{user_id}")
async def remove_drive_acl(
    user_id: int,
    folderId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    folder = (folderId or "").strip() or None
    if folder:
        await _folder_or_404(session, folder)
    await _require_level(session, user, folder, "manage")
    key = _acl_folder_id(folder)
    row = await session.get(LegalHubDriveAclModel, (key, user_id))
    if row is not None:
        await session.delete(row)
        await session.commit()
    return {"ok": True}
