
from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend_common.media_path import safe_media_path
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.day_files_repo import DayFilesRepository
from infrastructure.file_storage import delete_storage_file, save_day_file
from presentation.deps import get_current_user, is_admin_user

_log = logging.getLogger(__name__)

router = APIRouter(tags=["call_schedule_day_files"])


def _parse_day(raw: str) -> date:
    try:
        return date.fromisoformat((raw or "").strip()[:10])
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Некорректная дата (ожидается YYYY-MM-DD)") from e


class DayFileOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    day: str
    original_name: str = Field(serialization_alias="originalName")
    content_type: str | None = Field(None, serialization_alias="contentType")
    size_bytes: int = Field(serialization_alias="sizeBytes")
    uploaded_by_user_id: int = Field(serialization_alias="uploadedByUserId")
    uploaded_at: str = Field(serialization_alias="uploadedAt")


def _row_out(row) -> DayFileOut:
    uploaded = row.uploaded_at
    uploaded_s = uploaded.isoformat() if uploaded is not None else ""
    return DayFileOut(
        id=row.id,
        day=row.day.isoformat(),
        original_name=row.original_name,
        content_type=row.content_type,
        size_bytes=int(row.size_bytes or 0),
        uploaded_by_user_id=int(row.uploaded_by_user_id),
        uploaded_at=uploaded_s,
    )


@router.get("/days/files-counts", summary="Число файлов по дням за период")
async def day_files_counts(
    _: Annotated[dict, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    date_from: str = Query(..., alias="from", description="YYYY-MM-DD"),
    date_to: str = Query(..., alias="to", description="YYYY-MM-DD"),
) -> dict[str, Any]:
    d0, d1 = _parse_day(date_from), _parse_day(date_to)
    if d1 < d0:
        raise HTTPException(status_code=400, detail="to должен быть не раньше from")
    if (d1 - d0).days > 400:
        raise HTTPException(status_code=400, detail="Слишком большой диапазон")
    repo = DayFilesRepository(session)
    counts = await repo.counts_in_range(d0, d1)
    return {"counts": counts}


@router.get("/days/{day}/files", summary="Файлы дня", response_model=list[DayFileOut])
async def list_day_files(
    day: str,
    _: Annotated[dict, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
) -> list[DayFileOut]:
    d = _parse_day(day)
    repo = DayFilesRepository(session)
    rows = await repo.list_for_day(d)
    return [_row_out(r) for r in rows]


@router.post("/days/{day}/files", summary="Загрузить файл на день", response_model=DayFileOut)
async def upload_day_file(
    day: str,
    user: Annotated[dict, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    file: UploadFile = File(...),
) -> DayFileOut:
    d = _parse_day(day)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Пустой файл")
    try:
        storage_key, _safe = save_day_file(d.isoformat(), file.filename or "file", content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    file_id = str(uuid.uuid4())
    repo = DayFilesRepository(session)
    row = await repo.add(
        file_id=file_id,
        day=d,
        original_name=(file.filename or "file").strip() or "file",
        content_type=(file.content_type or None),
        size_bytes=len(content),
        storage_key=storage_key,
        uploaded_by_user_id=int(user["id"]),
    )
    await session.commit()
    return _row_out(row)


@router.get("/days/{day}/files/{file_id}/file", summary="Скачать файл дня")
async def download_day_file(
    day: str,
    file_id: str,
    _: Annotated[dict, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    d = _parse_day(day)
    repo = DayFilesRepository(session)
    row = await repo.get_by_id((file_id or "").strip())
    if not row or row.day != d:
        raise HTTPException(status_code=404, detail="Файл не найден")
    settings = get_settings()
    path = safe_media_path(settings.media_path, row.storage_key)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл на диске не найден")
    media = (row.content_type or "").strip() or "application/octet-stream"
    return FileResponse(
        path=path,
        filename=row.original_name or "file",
        media_type=media,
        content_disposition_type="attachment",
    )


@router.delete("/days/{day}/files/{file_id}", summary="Удалить файл дня")
async def delete_day_file(
    day: str,
    file_id: str,
    user: Annotated[dict, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    d = _parse_day(day)
    repo = DayFilesRepository(session)
    row = await repo.get_by_id((file_id or "").strip())
    if not row or row.day != d:
        raise HTTPException(status_code=404, detail="Файл не найден")
    uid = int(user["id"])
    if row.uploaded_by_user_id != uid and not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Удалить может только автор или администратор")
    storage_key = row.storage_key
    await repo.delete(row)
    await session.commit()
    delete_storage_file(storage_key)
    return {"ok": True, "id": file_id}
