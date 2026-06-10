from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.kind_legend import ALL_KIND_CODES, document_required_for
from infrastructure.config import get_settings
from infrastructure.models import (
    AbsenceDay,
    AbsenceDocument,
    ManualAbsenceEntry,
    ScheduleEmployee,
)

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024  # 25 MB на файл
MAX_DOCUMENTS_PER_ENTRY = 20

# Разрешённые расширения для документов-оснований.
ALLOWED_DOCUMENT_SUFFIXES = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".doc", ".docx", ".xls", ".xlsx", ".txt"}
)


@dataclass
class UploadedFile:
    filename: str
    content_type: str | None
    content: bytes


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _count_days_inclusive(d_from: date, d_to: date) -> int:
    if d_to < d_from:
        return 0
    return (d_to - d_from).days + 1


def _validate_file(f: UploadedFile) -> None:
    name = (f.filename or "").strip()
    if not name:
        raise ValueError("У файла-основания нет имени")
    if len(f.content) == 0:
        raise ValueError(f"Файл «{name}» пустой")
    if len(f.content) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"Файл «{name}» больше {MAX_DOCUMENT_BYTES // (1024 * 1024)} МБ")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_DOCUMENT_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_DOCUMENT_SUFFIXES))
        raise ValueError(f"Недопустимый тип файла «{name}». Разрешены: {allowed}")


def _media_base() -> Path:
    return Path(get_settings().media_path).resolve()


def _save_document_bytes(entry: ManualAbsenceEntry, original_name: str, data: bytes) -> str:
    base = _media_base()
    year = entry.date_from.year
    subdir = base / "vacation_absence_documents" / str(year) / str(entry.id)
    subdir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name).suffix.lower()
    name = f"doc_{uuid4().hex}{suffix}"
    target = subdir / name
    target.write_bytes(data)
    return f"vacation_absence_documents/{year}/{entry.id}/{name}"


def document_disk_path(doc: AbsenceDocument) -> Path | None:
    base = _media_base()
    target = (base / (doc.storage_key or "")).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        return None
    return target


async def _materialize_manual_days(session: AsyncSession, entry: ManualAbsenceEntry) -> None:
    """Создать/обновить записи в absence_days за каждый день периода ручной записи.

    Если на дату у сотрудника уже есть отметка — она перезаписывается на категорию
    ручной записи и привязывается к ней (manual_entry_id).
    """
    cur = entry.date_from
    while cur <= entry.date_to:
        existing = await session.execute(
            select(AbsenceDay).where(
                AbsenceDay.employee_id == entry.employee_id,
                AbsenceDay.absence_on == cur,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            session.add(
                AbsenceDay(
                    employee_id=entry.employee_id,
                    absence_on=cur,
                    kind_code=entry.kind_code,
                    manual_entry_id=entry.id,
                )
            )
        else:
            row.kind_code = entry.kind_code
            row.manual_entry_id = entry.id
            session.add(row)
        cur = cur + timedelta(days=1)
    await session.flush()


async def create_manual_entry(
    session: AsyncSession,
    *,
    employee: ScheduleEmployee,
    kind_code: int,
    date_from: date,
    date_to: date,
    reason: str | None,
    created_by_user_id: int | None,
    created_by_name: str | None,
    files: list[UploadedFile],
) -> ManualAbsenceEntry:
    if int(kind_code) not in ALL_KIND_CODES:
        raise ValueError("Недопустимая категория (kind_code)")
    if date_to < date_from:
        raise ValueError("date_to не может быть раньше date_from")
    if (date_to - date_from).days > 366:
        raise ValueError("Слишком длинный период")
    if date_from.year != employee.year or date_to.year != employee.year:
        raise ValueError(
            f"Период должен быть в пределах года графика сотрудника ({employee.year})"
        )

    if document_required_for(kind_code) and not files:
        raise ValueError(
            "Для ручной записи в график нужно приложить документ-основание "
            "(приказ/заявление/доп. документы) на выбранный период"
        )
    if len(files) > MAX_DOCUMENTS_PER_ENTRY:
        raise ValueError(f"Слишком много файлов (максимум {MAX_DOCUMENTS_PER_ENTRY})")
    for f in files:
        _validate_file(f)

    now = _utc_now()
    entry = ManualAbsenceEntry(
        employee_id=employee.id,
        kind_code=int(kind_code),
        date_from=date_from,
        date_to=date_to,
        reason=(reason or "").strip()[:4000] or None,
        created_by_user_id=created_by_user_id,
        created_by_name=(created_by_name or "").strip()[:500] or None,
        created_at=now,
        updated_at=None,
    )
    session.add(entry)
    await session.flush()  # нужен entry.id для путей к файлам

    for f in files:
        storage_key = _save_document_bytes(entry, f.filename, f.content)
        session.add(
            AbsenceDocument(
                manual_entry_id=entry.id,
                leave_request_id=None,
                storage_key=storage_key,
                original_filename=(f.filename or "").strip()[:500],
                content_type=(f.content_type or None),
                size_bytes=len(f.content),
                uploaded_by_user_id=created_by_user_id,
                created_at=now,
            )
        )

    await _materialize_manual_days(session, entry)
    await session.flush()
    return entry


async def _cleanup_entry_files(entry: ManualAbsenceEntry) -> None:
    base = _media_base()
    for doc in entry.documents:
        target = (base / (doc.storage_key or "")).resolve()
        if str(target).startswith(str(base)) and target.is_file():
            target.unlink(missing_ok=True)
    # удалить пустую директорию записи, если осталась
    try:
        year = entry.date_from.year
        subdir = (base / "vacation_absence_documents" / str(year) / str(entry.id)).resolve()
        if str(subdir).startswith(str(base)) and subdir.is_dir():
            subdir.rmdir()
    except OSError:
        pass


async def delete_manual_entry(session: AsyncSession, entry: ManualAbsenceEntry) -> None:
    await _cleanup_entry_files(entry)
    # absence_days привязаны через FK ON DELETE CASCADE, но удалим явно для предсказуемости
    await session.execute(
        delete(AbsenceDay).where(AbsenceDay.manual_entry_id == entry.id)
    )
    await session.delete(entry)
    await session.flush()
