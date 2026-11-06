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
from infrastructure.models import LegalHubDriveFileModel, LegalHubDriveFolderModel
from presentation.deps import require_user

router = APIRouter(tags=["legal-hub"])
MAX_FILE_BYTES = 15 * 1024 * 1024


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    parentId: str | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


def _user_id(user: dict) -> int:
    return int(user["id"])


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


@router.get("/drive")
async def list_drive(
    parentId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    parent = (parentId or "").strip() or None
    await _folder_or_404(session, parent)
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
    _: dict = Depends(require_user),
):
    item = await session.get(LegalHubDriveFileModel, file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return Response(
        content=item.content,
        media_type=item.content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(item.name)}"},
    )


@router.delete("/drive/files/{file_id}")
async def delete_drive_file(
    file_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    item = await session.get(LegalHubDriveFileModel, file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    parent = item.folder_id
    await session.delete(item)
    await session.commit()
    return await list_drive(parent, session, _)


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
    await _delete_folder_tree(session, folder.id)
    await session.commit()
    return await list_drive(parent, session, user)
