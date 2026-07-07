from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import _can_manage_tt
from infrastructure.models import TimeEntryModel
from infrastructure.repository_entry_archives import TimeEntryArchiveRepository
from infrastructure.repository_entries import TimeEntryRepository
from infrastructure.repository_invoices import InvoiceRepository
from infrastructure.report_cache import invalidate_all_reports

ARCHIVE_VOID_KIND = "duplicate_archive"


def _entry_payload(
    row: TimeEntryModel,
    *,
    user_name: str | None = None,
    task_name: str | None = None,
    duplicate_group_id: str | None = None,
) -> dict[str, Any]:
    return {
        "time_entry_id": row.id,
        "auth_user_id": row.auth_user_id,
        "work_date": row.work_date.isoformat(),
        "hours": str(row.hours),
        "rounded_hours": str(row.rounded_hours),
        "duration_seconds": row.duration_seconds,
        "is_billable": row.is_billable,
        "project_id": row.project_id,
        "task_id": row.task_id,
        "description": row.description,
        "external_reference_url": row.external_reference_url,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "user_name": user_name,
        "task_name": task_name,
        "duplicate_group_id": duplicate_group_id,
    }


async def archive_duplicate_entries(
    session: AsyncSession,
    viewer: dict,
    *,
    project_id: str,
    client_id: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    if not _can_manage_tt(viewer):
        raise PermissionError("Недостаточно прав для архивации дубликатов")

    viewer_id = int(viewer["id"])
    entry_repo = TimeEntryRepository(session)
    archive_repo = TimeEntryArchiveRepository(session)
    inv_repo = InvoiceRepository(session)

    archived: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in entries:
        auth_user_id = int(item["auth_user_id"])
        entry_id = str(item["entry_id"]).strip()
        duplicate_group_id = item.get("duplicate_group_id")

        row = await entry_repo.get_by_id(auth_user_id, entry_id)
        if not row:
            skipped.append({"entry_id": entry_id, "reason": "not_found"})
            continue
        if str(row.project_id or "") != str(project_id):
            skipped.append({"entry_id": entry_id, "reason": "wrong_project"})
            continue
        if row.voided_at is not None:
            skipped.append({"entry_id": entry_id, "reason": "already_voided"})
            continue
        if await inv_repo.time_entry_on_active_invoice(entry_id):
            skipped.append({"entry_id": entry_id, "reason": "on_invoice"})
            continue

        payload = _entry_payload(
            row,
            user_name=item.get("user_name"),
            task_name=item.get("task_name"),
            duplicate_group_id=str(duplicate_group_id) if duplicate_group_id else None,
        )
        archive_row = await archive_repo.create(
            time_entry_id=entry_id,
            auth_user_id=auth_user_id,
            project_id=project_id,
            client_id=client_id,
            duplicate_group_id=str(duplicate_group_id) if duplicate_group_id else None,
            archived_by_auth_user_id=viewer_id,
            payload=payload,
        )
        await entry_repo.void_entry(
            auth_user_id,
            entry_id,
            voided_by_auth_user_id=viewer_id,
            void_kind=ARCHIVE_VOID_KIND,
        )
        archived.append(TimeEntryArchiveRepository.to_api(archive_row))

    await session.commit()
    invalidate_all_reports()
    return {
        "archived": archived,
        "skipped": skipped,
        "archived_count": len(archived),
        "skipped_count": len(skipped),
    }


async def restore_archived_entry(
    session: AsyncSession,
    viewer: dict,
    *,
    archive_id: str,
    project_id: str,
) -> dict[str, Any]:
    if not _can_manage_tt(viewer):
        raise PermissionError("Недостаточно прав для восстановления записи")

    viewer_id = int(viewer["id"])
    archive_repo = TimeEntryArchiveRepository(session)
    entry_repo = TimeEntryRepository(session)

    archive_row = await archive_repo.get_by_id(archive_id)
    if not archive_row or str(archive_row.project_id or "") != str(project_id):
        raise LookupError("archive_not_found")
    if archive_row.restored_at is not None:
        raise ValueError("Запись уже восстановлена")

    row = await entry_repo.get_by_id(archive_row.auth_user_id, archive_row.time_entry_id)
    if not row:
        raise LookupError("time_entry_not_found")
    if row.voided_at is None:
        raise ValueError("Запись не в архиве (не void)")
    if (row.void_kind or "") != ARCHIVE_VOID_KIND:
        raise ValueError("Запись снята с учёта не как дубликат — восстановление только из архива дубликатов")

    await entry_repo.restore_entry(archive_row.auth_user_id, archive_row.time_entry_id)
    restored_archive = await archive_repo.mark_restored(
        archive_id,
        restored_by_auth_user_id=viewer_id,
    )
    await session.commit()
    invalidate_all_reports()
    return {
        "archive": TimeEntryArchiveRepository.to_api(restored_archive) if restored_archive else None,
        "time_entry_id": archive_row.time_entry_id,
        "auth_user_id": archive_row.auth_user_id,
    }
