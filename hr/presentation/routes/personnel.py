from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import PersonnelFileModel
from infrastructure.personnel_files import (
    DOCUMENT_SLOTS,
    PHOTO_SLOT,
    delete_personnel_bytes,
    is_image_file,
    normalize_slot,
    personnel_disk_path,
    personnel_storage_key,
    save_personnel_bytes,
    validate_upload,
)
from presentation.deps import require_hr_user

router = APIRouter(prefix="/personnel", tags=["personnel"])


def _media_base() -> Path:
    return Path(get_settings().media_path).resolve()


def _personnel_file(storage_key: str) -> Path | None:
    found = personnel_disk_path(_media_base(), storage_key)
    if found is not None:
        return found
    legacy = Path("/app/media")
    try:
        if legacy.resolve() == _media_base():
            return None
    except OSError:
        return None
    return personnel_disk_path(legacy, storage_key)


def _payload(row: PersonnelFileModel) -> dict:
    return {
        "id": row.id,
        "userId": row.user_id,
        "slot": row.slot,
        "filename": row.original_filename,
        "contentType": row.content_type,
        "byteSize": row.byte_size,
        "isPrimary": bool(row.is_primary),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _check_user_id(user_id: int) -> int:
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Некорректный сотрудник")
    return user_id


async def _load_file(session: AsyncSession, user_id: int, file_id: str) -> PersonnelFileModel:
    row = await session.get(PersonnelFileModel, file_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return row


async def _clear_primary(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(PersonnelFileModel)
        .where(PersonnelFileModel.user_id == user_id, PersonnelFileModel.is_primary.is_(True))
        .values(is_primary=False)
    )


@router.get("/photos")
async def list_primary_photos(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    rows = list((await session.execute(
        select(PersonnelFileModel)
        .where(PersonnelFileModel.is_primary.is_(True))
        .order_by(PersonnelFileModel.created_at.desc())
    )).scalars().all())
    seen: set[int] = set()
    items: list[dict] = []
    for row in rows:
        if row.user_id in seen:
            continue
        seen.add(row.user_id)
        items.append({"userId": row.user_id, "updatedAt": row.created_at.isoformat() if row.created_at else row.id})
    return {"items": items}


@router.get("/{user_id}/files")
async def list_personnel_files(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    _check_user_id(user_id)
    rows = list((await session.execute(
        select(PersonnelFileModel)
        .where(PersonnelFileModel.user_id == user_id)
        .order_by(PersonnelFileModel.created_at.desc())
    )).scalars().all())
    return {"items": [_payload(row) for row in rows]}


@router.post("/{user_id}/files", status_code=201)
async def upload_personnel_file(
    user_id: int,
    slot: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    _actor: dict = Depends(require_hr_user),
):
    _check_user_id(user_id)
    try:
        cleaned_slot = normalize_slot(slot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if cleaned_slot not in DOCUMENT_SLOTS:
        raise HTTPException(status_code=400, detail="Для главного фото используйте загрузку фото")
    data = await file.read()
    filename = (file.filename or "file").strip()[:500]
    try:
        validate_upload(filename=filename, content=data, slot=cleaned_slot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    file_id = uuid4().hex
    storage_key = personnel_storage_key(user_id, file_id, filename)
    try:
        save_personnel_bytes(_media_base(), storage_key, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = PersonnelFileModel(
        id=file_id,
        user_id=user_id,
        slot=cleaned_slot,
        original_filename=filename,
        content_type=(file.content_type or None),
        storage_key=storage_key,
        is_primary=False,
        byte_size=len(data),
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _payload(row)


@router.post("/{user_id}/photo", status_code=201)
async def upload_primary_photo(
    user_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    _check_user_id(user_id)
    data = await file.read()
    filename = (file.filename or "photo").strip()[:500]
    try:
        validate_upload(filename=filename, content=data, slot=PHOTO_SLOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    file_id = uuid4().hex
    storage_key = personnel_storage_key(user_id, file_id, filename)
    try:
        save_personnel_bytes(_media_base(), storage_key, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    previous = list((await session.execute(
        select(PersonnelFileModel).where(
            PersonnelFileModel.user_id == user_id,
            PersonnelFileModel.slot == PHOTO_SLOT,
        )
    )).scalars().all())
    await _clear_primary(session, user_id)
    row = PersonnelFileModel(
        id=file_id,
        user_id=user_id,
        slot=PHOTO_SLOT,
        original_filename=filename,
        content_type=(file.content_type or "image/jpeg"),
        storage_key=storage_key,
        is_primary=True,
        byte_size=len(data),
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    for old in previous:
        delete_personnel_bytes(_media_base(), old.storage_key)
        await session.delete(old)
    await session.commit()
    await session.refresh(row)
    return _payload(row)


@router.post("/{user_id}/files/{file_id}/primary")
async def set_primary_photo(
    user_id: int,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    row = await _load_file(session, _check_user_id(user_id), file_id)
    if not is_image_file(row.original_filename, row.content_type):
        raise HTTPException(status_code=400, detail="Главным фото можно сделать только изображение")
    await _clear_primary(session, user_id)
    row.is_primary = True
    await session.commit()
    await session.refresh(row)
    return _payload(row)


@router.get("/{user_id}/photo")
async def get_primary_photo(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    _check_user_id(user_id)
    row = (await session.execute(
        select(PersonnelFileModel)
        .where(PersonnelFileModel.user_id == user_id, PersonnelFileModel.is_primary.is_(True))
        .order_by(PersonnelFileModel.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Главное фото не задано")
    target = _personnel_file(row.storage_key)
    if target is None:
        raise HTTPException(status_code=404, detail="Файл фото не найден")
    return FileResponse(target, media_type=row.content_type or "image/jpeg")


@router.get("/{user_id}/files/{file_id}")
async def download_personnel_file(
    user_id: int,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    row = await _load_file(session, _check_user_id(user_id), file_id)
    target = _personnel_file(row.storage_key)
    if target is None:
        raise HTTPException(status_code=404, detail="Файл не найден на диске")
    return FileResponse(
        target,
        media_type=row.content_type or "application/octet-stream",
        filename=row.original_filename or "file",
    )


@router.delete("/{user_id}/files/{file_id}", status_code=204)
async def delete_personnel_file(
    user_id: int,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    row = await _load_file(session, _check_user_id(user_id), file_id)
    delete_personnel_bytes(_media_base(), row.storage_key)
    await session.delete(row)
    await session.commit()
