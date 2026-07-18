from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import _can_manage_tt
from application.duplicate_time_entries import _entry_sort_key_for_keeper, _norm_note
from application.entry_pricing import _billable_amount_for_entry
from application.report_builder import _d as dec
from application.report_builder import _load_user_rates
from infrastructure.models import TimeEntryModel, TimeManagerClientTaskModel
from infrastructure.repositories import ClientProjectRepository
from infrastructure.repository_entry_archives import TimeEntryArchiveRepository
from infrastructure.repository_entries import TimeEntryRepository
from infrastructure.repository_invoices import InvoiceRepository
from infrastructure.report_cache import invalidate_all_reports

ARCHIVE_VOID_KIND = "duplicate_archive"

_Q2 = Decimal("0.01")
_Q6 = Decimal("0.000001")
# Системный пользователь для авто-архива при подтверждении отчёта (нет конкретного viewer).
AUTO_ARCHIVE_SYSTEM_USER_ID = 0


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


def _report_dup_fingerprint(
    entry: TimeEntryModel,
    task: Any | None,
    *,
    amount: Decimal,
    currency: str,
) -> str:
    """Отпечаток строки как в ФИНАЛЬНОМ дедупе просмотра отчёта
    (`deduplicateTimeExcelPreviewRows` на фронте): сотрудник + дата + ИМЯ задачи +
    заметка + часы(6 зн.) + сумма(2 зн.) + валюта.

    Задача сравнивается по ИМЕНИ (а не `task_id`) — ловим дубли с разными карточками
    задачи, но одинаковым названием: именно их отчёт схлопывает на экране, а в БД они
    оставались живыми. Авто-архив гасит ровно то, что отчёт скрывает."""
    task_name = _norm_note(getattr(task, "name", None))
    note = _norm_note(getattr(entry, "description", None))
    hours_key = str(dec(entry.hours).quantize(_Q6, rounding=ROUND_HALF_UP))
    amount_key = str(amount.quantize(_Q2, rounding=ROUND_HALF_UP))
    return "\x1f".join(
        (
            f"id:{entry.auth_user_id}",
            entry.work_date.isoformat() if entry.work_date else "",
            task_name,
            note,
            hours_key,
            amount_key,
            (currency or "").strip().upper()[:10],
        )
    )


async def _load_project_tasks_map(
    session: AsyncSession, task_ids: set[str]
) -> dict[str, TimeManagerClientTaskModel]:
    ids = [t for t in task_ids if t]
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(TimeManagerClientTaskModel).where(
                TimeManagerClientTaskModel.id.in_(ids)
            )
        )
    ).scalars().all()
    return {str(r.id): r for r in rows}


async def auto_archive_duplicates_for_project_period(
    session: AsyncSession,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
    archived_by_auth_user_id: int = AUTO_ARCHIVE_SYSTEM_USER_ID,
    client_id: str | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Гасит (архивирует) дубли записей проекта за период — ровно те, что отчёт
    схлопывает на экране. Держит БД в синхроне с подтверждённым отчётом.

    Механика — как у ручной архивации из вкладки «Дубликаты»: void записи
    (`void_kind = "duplicate_archive"`) + снимок в `time_tracking_entry_archives`,
    поэтому действие ОБРАТИМО через «Восстановить».

    В каждой группе дублей оставляем самую раннюю запись (keeper), остальные — в архив.
    Записи, попавшие в активный счёт, НЕ трогаем (пропускаем).

    Не коммитит по умолчанию (вызывающий код коммитит в своей транзакции).
    Кэш отчётов инвалидируется только при commit=True.
    """
    pid = (project_id or "").strip()
    if not pid or date_to < date_from:
        return {"archived_count": 0, "skipped_count": 0, "group_count": 0, "archived": [], "skipped": []}

    entry_repo = TimeEntryRepository(session)
    archive_repo = TimeEntryArchiveRepository(session)
    inv_repo = InvoiceRepository(session)
    proj_repo = ClientProjectRepository(session)

    entries = await entry_repo.list_entries_for_project(pid, date_from, date_to)
    if len(entries) < 2:
        return {"archived_count": 0, "skipped_count": 0, "group_count": 0, "archived": [], "skipped": []}

    proj = await proj_repo.get_by_id_global(pid)
    project_ccy = (getattr(proj, "currency", None) or "USD").strip() or "USD"
    resolved_client_id = client_id or (getattr(proj, "client_id", None) if proj else None)

    rates = await _load_user_rates(session, sorted({e.auth_user_id for e in entries}))
    tasks_map = await _load_project_tasks_map(
        session, {str(e.task_id) for e in entries if e.task_id}
    )

    groups: dict[str, list[TimeEntryModel]] = defaultdict(list)
    task_by_entry: dict[str, Any] = {}
    for e in entries:
        task = tasks_map.get(str(e.task_id)) if e.task_id else None
        task_by_entry[e.id] = task
        amt, cur = _billable_amount_for_entry(
            dec(e.hours),
            bool(e.is_billable),
            e.work_date,
            rates.get(e.auth_user_id),
            project_currency=project_ccy,
            time_entry_project_id=e.project_id,
            task=task,
        )
        fp = _report_dup_fingerprint(e, task, amount=amt, currency=cur or project_ccy)
        groups[fp].append(e)

    archived: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    dup_group_count = 0

    for fp, group in groups.items():
        if len(group) < 2:
            continue
        dup_group_count += 1
        # keeper — самая ранняя запись (как в ручной архивации / дедупе отчёта)
        ordered = sorted(group, key=_entry_sort_key_for_keeper)
        for e in ordered[1:]:
            if await inv_repo.time_entry_on_active_invoice(e.id):
                skipped.append({"entry_id": e.id, "reason": "on_invoice"})
                continue
            task = task_by_entry.get(e.id)
            payload = _entry_payload(
                e,
                task_name=getattr(task, "name", None),
                duplicate_group_id=fp,
            )
            archive_row = await archive_repo.create(
                time_entry_id=e.id,
                auth_user_id=e.auth_user_id,
                project_id=pid,
                client_id=resolved_client_id,
                duplicate_group_id=fp,
                archived_by_auth_user_id=int(archived_by_auth_user_id),
                payload=payload,
            )
            await entry_repo.void_entry(
                e.auth_user_id,
                e.id,
                voided_by_auth_user_id=int(archived_by_auth_user_id),
                void_kind=ARCHIVE_VOID_KIND,
            )
            archived.append(TimeEntryArchiveRepository.to_api(archive_row))

    if commit and (archived or skipped):
        await session.commit()
        invalidate_all_reports()

    return {
        "archived": archived,
        "skipped": skipped,
        "archived_count": len(archived),
        "skipped_count": len(skipped),
        "group_count": dup_group_count,
    }
