from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from application.kind_legend import (
    ALL_KIND_CODES,
    KIND_BY_CODE,
    KIND_BY_KEY,
    KIND_LABELS_RU,
    document_required_for,
)
from application.manual_absence_service import (
    UploadedFile,
    create_manual_entry,
    delete_manual_entry,
    document_disk_path,
)
from infrastructure.auth_lookup import AuthUser, get_me
from infrastructure.database import get_session
from infrastructure.models import (
    AbsenceDocument,
    ManualAbsenceEntry,
    ScheduleEmployee,
)

router = APIRouter(prefix="/schedule/manual-entries", tags=["manual-entries"])


async def _resolve_uploader(authorization: str | None) -> AuthUser | None:
    """Определить, кто вносит запись. Доступ уже проверен на gateway, поэтому
    ошибки авторизации тут не блокируют — просто оставляем автора пустым."""
    if not authorization or not authorization.strip():
        return None
    try:
        return await get_me(authorization.strip())
    except HTTPException:
        return None


class DocumentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    original_filename: str = Field(..., alias="originalFilename")
    content_type: str | None = Field(None, alias="contentType")
    size_bytes: int = Field(..., alias="sizeBytes")
    download_url: str = Field(..., alias="downloadUrl")
    created_at: datetime = Field(..., alias="createdAt")


class ManualEntryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    employee_id: int = Field(..., alias="employeeId")
    kind_code: int = Field(..., alias="kindCode")
    kind: str
    label_ru: str = Field(..., alias="labelRu")
    date_from: date = Field(..., alias="dateFrom")
    date_to: date = Field(..., alias="dateTo")
    reason: str | None = None
    created_by_user_id: int | None = Field(None, alias="createdByUserId")
    created_by_name: str | None = Field(None, alias="createdByName")
    created_at: datetime = Field(..., alias="createdAt")
    documents: list[DocumentOut]


def _doc_download_url(entry_id: int, doc_id: int) -> str:
    return f"/api/v1/vacations/schedule/manual-entries/{entry_id}/documents/{doc_id}/download"


def _doc_out(entry_id: int, d: AbsenceDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        original_filename=d.original_filename,
        content_type=d.content_type,
        size_bytes=d.size_bytes,
        download_url=_doc_download_url(entry_id, d.id),
        created_at=d.created_at,
    )


def _entry_out(entry: ManualAbsenceEntry) -> ManualEntryOut:
    docs = sorted(entry.documents, key=lambda d: d.id)
    return ManualEntryOut(
        id=entry.id,
        employee_id=entry.employee_id,
        kind_code=entry.kind_code,
        kind=KIND_BY_CODE.get(entry.kind_code, "unknown"),
        label_ru=KIND_LABELS_RU.get(entry.kind_code, "—"),
        date_from=entry.date_from,
        date_to=entry.date_to,
        reason=entry.reason,
        created_by_user_id=entry.created_by_user_id,
        created_by_name=entry.created_by_name,
        created_at=entry.created_at,
        documents=[_doc_out(entry.id, d) for d in docs],
    )


def _resolve_kind_code(kind: str | None, kind_code: int | None) -> int:
    if kind_code is not None:
        if int(kind_code) not in ALL_KIND_CODES:
            raise HTTPException(status_code=400, detail="Недопустимый kind_code")
        return int(kind_code)
    if kind:
        if kind not in KIND_BY_KEY:
            allowed = ", ".join(KIND_BY_KEY.keys())
            raise HTTPException(status_code=400, detail=f"kind должен быть одним из: {allowed}")
        return KIND_BY_KEY[kind]
    raise HTTPException(status_code=400, detail="Укажите kind или kindCode")


async def _load_entry(session: AsyncSession, entry_id: int) -> ManualAbsenceEntry:
    r = await session.execute(
        select(ManualAbsenceEntry)
        .options(selectinload(ManualAbsenceEntry.documents))
        .where(ManualAbsenceEntry.id == entry_id)
    )
    entry = r.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Ручная запись не найдена")
    return entry


async def _read_uploads(files: list[UploadFile] | None) -> list[UploadedFile]:
    out: list[UploadedFile] = []
    for f in files or []:
        if f is None:
            continue
        data = await f.read()
        if not data:
            continue
        out.append(
            UploadedFile(
                filename=(f.filename or "file"),
                content_type=(f.content_type or None),
                content=data,
            )
        )
    return out


@router.get("", response_model=list[ManualEntryOut])
async def list_manual_entries(
    year: int | None = Query(None, ge=2000, le=2100),
    employee_id: int | None = Query(None, alias="employeeId"),
    session: AsyncSession = Depends(get_session),
):
    q = (
        select(ManualAbsenceEntry)
        .options(selectinload(ManualAbsenceEntry.documents))
        .join(ScheduleEmployee, ScheduleEmployee.id == ManualAbsenceEntry.employee_id)
    )
    if year is not None:
        q = q.where(ScheduleEmployee.year == year)
    if employee_id is not None:
        q = q.where(ManualAbsenceEntry.employee_id == employee_id)
    q = q.order_by(ManualAbsenceEntry.date_from.desc(), ManualAbsenceEntry.id.desc())
    r = await session.execute(q)
    return [_entry_out(e) for e in r.scalars().all()]


@router.get("/{entry_id}", response_model=ManualEntryOut)
async def get_manual_entry(
    entry_id: int,
    session: AsyncSession = Depends(get_session),
):
    entry = await _load_entry(session, entry_id)
    return _entry_out(entry)


@router.post("", response_model=ManualEntryOut, status_code=201)
async def create_manual_entry_endpoint(
    employee_id: Annotated[int, Form(alias="employeeId")],
    date_from: Annotated[date, Form(alias="dateFrom")],
    date_to: Annotated[date, Form(alias="dateTo")],
    kind: Annotated[Optional[str], Form()] = None,
    kind_code: Annotated[Optional[int], Form(alias="kindCode")] = None,
    reason: Annotated[Optional[str], Form()] = None,
    files: Annotated[Optional[list[UploadFile]], File()] = None,
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    session: AsyncSession = Depends(get_session),
):
    code = _resolve_kind_code(kind, kind_code)

    r = await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.id == employee_id))
    employee = r.scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=404, detail="Сотрудник графика не найден")

    uploads = await _read_uploads(files)
    uploader = await _resolve_uploader(authorization)

    try:
        entry = await create_manual_entry(
            session,
            employee=employee,
            kind_code=code,
            date_from=date_from,
            date_to=date_to,
            reason=reason,
            created_by_user_id=(uploader.id if uploader else None),
            created_by_name=(uploader.display_name or uploader.email if uploader else None),
            files=uploads,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    entry = await _load_entry(session, entry.id)
    return _entry_out(entry)


@router.post("/{entry_id}/documents", response_model=ManualEntryOut)
async def add_documents_endpoint(
    entry_id: int,
    files: Annotated[list[UploadFile], File()],
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    session: AsyncSession = Depends(get_session),
):
    entry = await _load_entry(session, entry_id)
    uploads = await _read_uploads(files)
    if not uploads:
        raise HTTPException(status_code=400, detail="Нет файлов для загрузки")

    from application.manual_absence_service import (                                            
        MAX_DOCUMENTS_PER_ENTRY,
        _save_document_bytes,
        _validate_file,
    )
    from datetime import timezone

    if len(entry.documents) + len(uploads) > MAX_DOCUMENTS_PER_ENTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много файлов (максимум {MAX_DOCUMENTS_PER_ENTRY})",
        )
    uploader = await _resolve_uploader(authorization)
    now = datetime.now(timezone.utc)
    try:
        for f in uploads:
            _validate_file(f)
        for f in uploads:
            storage_key = _save_document_bytes(entry, f.filename, f.content)
            session.add(
                AbsenceDocument(
                    manual_entry_id=entry.id,
                    leave_request_id=None,
                    storage_key=storage_key,
                    original_filename=(f.filename or "").strip()[:500],
                    content_type=(f.content_type or None),
                    size_bytes=len(f.content),
                    uploaded_by_user_id=(uploader.id if uploader else None),
                    created_at=now,
                )
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    entry = await _load_entry(session, entry_id)
    return _entry_out(entry)


@router.get("/{entry_id}/documents/{doc_id}/download")
async def download_document(
    entry_id: int,
    doc_id: int,
    session: AsyncSession = Depends(get_session),
):
    r = await session.execute(
        select(AbsenceDocument).where(
            AbsenceDocument.id == doc_id,
            AbsenceDocument.manual_entry_id == entry_id,
        )
    )
    doc = r.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    path = document_disk_path(doc)
    if path is None:
        raise HTTPException(status_code=404, detail="Файл недоступен")
    return FileResponse(
        path,
        media_type=(doc.content_type or "application/octet-stream"),
        filename=doc.original_filename or f"document_{doc.id}",
    )


@router.delete("/{entry_id}/documents/{doc_id}", response_model=ManualEntryOut)
async def delete_document(
    entry_id: int,
    doc_id: int,
    session: AsyncSession = Depends(get_session),
):
    entry = await _load_entry(session, entry_id)
    doc = next((d for d in entry.documents if d.id == doc_id), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if document_required_for(entry.kind_code) and len(entry.documents) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Нельзя удалить последнее основание: для этой категории документ обязателен",
        )
    path = document_disk_path(doc)
    if path is not None:
        path.unlink(missing_ok=True)
    await session.delete(doc)
    await session.commit()
    entry = await _load_entry(session, entry_id)
    return _entry_out(entry)


@router.delete("/{entry_id}", status_code=204)
async def delete_manual_entry_endpoint(
    entry_id: int,
    session: AsyncSession = Depends(get_session),
):
    entry = await _load_entry(session, entry_id)
    await delete_manual_entry(session, entry)
    await session.commit()
