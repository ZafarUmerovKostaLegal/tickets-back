from __future__ import annotations

"""Поиск дубликатов записей времени внутри проекта.

Дубликат = один сотрудник + день учёта (work_date) + задача + заметка + часы + сумма.
Время появления записи в системе (created_at) не учитывается.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from application.entry_pricing import _billable_amount_for_entry
from application.note_normalize import (
    normalize_note_for_duplicate_key,
    notes_are_near_duplicate,
    task_name_from_row,
)
from application.report_builder import _load_user_rates
from application.task_billing import is_flat_fee_task
from application.time_rounding import round_decimal_hours_to_minute
from application.user_initials import resolve_user_initials
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientTaskModel,
    TimeTrackingUserModel,
)
from infrastructure.repository_entries import TimeEntryRepository

_Q6 = Decimal("0.000001")
_Q2 = Decimal("0.01")


def _norm_note(value: str | None, task_name: str | None = None) -> str:
    return normalize_note_for_duplicate_key(value, task_name)


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal(0)


def _hours_key(v: Decimal) -> str:
    return str(v.quantize(_Q6, rounding=ROUND_HALF_UP))


def _money_key(v: Decimal) -> str:
    return str(v.quantize(_Q2, rounding=ROUND_HALF_UP))



@dataclass(frozen=True)
class DuplicateKey:
    auth_user_id: int
    work_date: date
    task_id: str
    note_norm: str
    hours_key: str
    amount_key: str
    currency: str

    def as_group_id(self) -> str:
        return "|".join(
            (
                str(self.auth_user_id),
                self.work_date.isoformat(),
                self.task_id,
                self.note_norm,
                self.hours_key,
                self.amount_key,
                self.currency,
            )
        )


def build_duplicate_key_for_entry(
    e: TimeEntryModel,
    *,
    project_currency: str,
    rates_map: dict[int, list],
    task: Any | None = None,
    package_split: Any | None = None,
    ignore_amount: bool = False,
) -> DuplicateKey:
    raw_hrs = _d(e.rounded_hours if e.rounded_hours is not None else e.hours)
    # Align with report preview FE (`roundDecimalHoursToMinute`).
    hrs = round_decimal_hours_to_minute(raw_hrs)
    if package_split is not None and not is_flat_fee_task(task):
        bill_hrs = round_decimal_hours_to_minute(_d(getattr(package_split, "overage_hours", 0)))
        amt, cur = _billable_amount_for_entry(
            bill_hrs,
            bool(e.is_billable) and bill_hrs > 0,
            e.work_date,
            rates_map.get(e.auth_user_id),
            project_currency=project_currency,
            time_entry_project_id=e.project_id,
            task=task,
        )
    else:
        amt, cur = _billable_amount_for_entry(
            hrs,
            e.is_billable,
            e.work_date,
            rates_map.get(e.auth_user_id),
            project_currency=project_currency,
            time_entry_project_id=e.project_id,
            task=task,
        )
    return DuplicateKey(
        auth_user_id=int(e.auth_user_id),
        work_date=e.work_date,
        task_id=(e.task_id or "").strip(),
        note_norm=_norm_note(e.description, task_name_from_row(task)),
        hours_key=_hours_key(hrs),
        amount_key="*" if ignore_amount else _money_key(_d(amt)),
        currency=(cur or project_currency or "USD").strip()[:10],
    )


def _entry_sort_key_for_keeper(e: TimeEntryModel) -> tuple:
    created = e.created_at
    if created is None:
        return (e.work_date, e.id)
    return (created, e.id)


def _duplicate_meta_key(dk: DuplicateKey) -> tuple:
    """All fingerprint fields except note — for near-duplicate note clustering."""
    return (
        dk.auth_user_id,
        dk.work_date,
        dk.task_id,
        dk.hours_key,
        dk.amount_key,
        dk.currency,
    )


def _merge_near_duplicate_note_groups(
    by_group: dict[tuple[str, DuplicateKey], list[TimeEntryModel]],
) -> dict[tuple[str, DuplicateKey], list[TimeEntryModel]]:
    """Merge groups that match on user/date/task/hours/amount but have near-equal notes."""
    buckets: dict[tuple[str, tuple], list[tuple[DuplicateKey, list[TimeEntryModel]]]] = defaultdict(list)
    for (pid, dk), group in by_group.items():
        buckets[(pid, _duplicate_meta_key(dk))].append((dk, group))

    out: dict[tuple[str, DuplicateKey], list[TimeEntryModel]] = {}
    for (pid, _meta), items in buckets.items():
        clusters: list[tuple[DuplicateKey, list[TimeEntryModel]]] = []
        for dk, group in items:
            placed = False
            for i, (cdk, cgroup) in enumerate(clusters):
                if notes_are_near_duplicate(dk.note_norm, cdk.note_norm):
                    # Prefer longer note as cluster key label.
                    key_dk = cdk if len(cdk.note_norm) >= len(dk.note_norm) else dk
                    clusters[i] = (key_dk, cgroup + group)
                    placed = True
                    break
            if not placed:
                clusters.append((dk, list(group)))
        for dk, group in clusters:
            out[(pid, dk)] = group
    return out


def deduplicate_entries_for_report(
    entries: list[TimeEntryModel],
    *,
    projects_map: dict[str, Any],
    rates_map: dict[int, list],
    tasks_map: dict[str, Any] | None = None,
    package_splits: dict[str, Any] | None = None,
    ignore_amount: bool = False,
) -> tuple[list[TimeEntryModel], int]:
    """Убрать дубликаты из отчёта: в каждой группе оставить самую раннюю запись (как при архивации)."""
    if not entries:
        return [], 0

    by_group: dict[tuple[str, DuplicateKey], list[TimeEntryModel]] = defaultdict(list)
    for e in entries:
        p = projects_map.get(e.project_id) if e.project_id else None
        cur = (getattr(p, "currency", None) or "USD") if p else "USD"
        task = tasks_map.get(e.task_id) if tasks_map and e.task_id else None
        split = (package_splits or {}).get(str(e.id)) if package_splits else None
        dk = build_duplicate_key_for_entry(
            e,
            project_currency=cur,
            rates_map=rates_map,
            task=task,
            package_split=split,
            ignore_amount=ignore_amount,
        )
        pid = (e.project_id or "").strip()
        by_group[(pid, dk)].append(e)

    by_group = _merge_near_duplicate_note_groups(by_group)

    kept: list[TimeEntryModel] = []
    dropped = 0
    for group in by_group.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        keeper = min(group, key=_entry_sort_key_for_keeper)
        kept.append(keeper)
        dropped += len(group) - 1
    return kept, dropped


def _entry_to_duplicate_row(
    e: TimeEntryModel,
    *,
    u: TimeTrackingUserModel | None,
    t: TimeManagerClientTaskModel | None,
    project_currency: str,
    rates_map: dict[int, list],
    initials_map: dict[int, str | None] | None = None,
) -> tuple[DuplicateKey, dict[str, Any]]:
    hrs = _d(e.rounded_hours if e.rounded_hours is not None else e.hours)
    amt, cur = _billable_amount_for_entry(
        hrs,
        e.is_billable,
        e.work_date,
        rates_map.get(e.auth_user_id),
        project_currency=project_currency,
        time_entry_project_id=e.project_id,
        task=t,
    )
    key = build_duplicate_key_for_entry(
        e,
        project_currency=project_currency,
        rates_map=rates_map,
        task=t,
    )
    user_name = (u.display_name or u.email or str(e.auth_user_id)) if u else str(e.auth_user_id)
    row = {
        "entry_id": e.id,
        "auth_user_id": int(e.auth_user_id),
        "user_name": user_name,
        "user_initials": resolve_user_initials(u, initials_map=initials_map or {}),
        "work_date": e.work_date.isoformat(),
        "task_id": e.task_id,
        "task_name": t.name if t else "",
        "description": (e.description or "").strip(),
        "hours": float(_d(e.hours)),
        "rounded_hours": float(hrs),
        "is_billable": bool(e.is_billable),
        "billable_amount": float(_d(amt)),
        "currency": key.currency,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
    return key, row


def split_duplicate_groups_by_work_date(
    groups: list[dict[str, Any]],
    *,
    min_group_size: int = 2,
) -> list[dict[str, Any]]:
    """Разбить группы, если в одной оказались записи с разными work_date (защита)."""
    out: list[dict[str, Any]] = []
    for group in groups:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        fallback = str(group.get("work_date") or "").strip()[:10]
        for entry in group.get("entries") or []:
            wd = str(entry.get("work_date") or fallback or "").strip()[:10] or "__unknown__"
            buckets[wd].append(entry)
        if len(buckets) <= 1:
            if len(group.get("entries") or []) >= min_group_size:
                out.append(group)
            continue
        part = 0
        for bucket_key, entries in buckets.items():
            if len(entries) < min_group_size:
                continue
            part += 1
            entries_sorted = sorted(
                entries, key=lambda r: (r.get("created_at") or "", r.get("entry_id") or "")
            )
            first = entries_sorted[0]
            wd = bucket_key if bucket_key != "__unknown__" else str(first.get("work_date") or fallback)
            out.append(
                {
                    **group,
                    "group_id": f"{group.get('group_id', '')}__{bucket_key}",
                    "group_label": f"{group.get('group_label', 'DUP')}__{part}",
                    "work_date": wd,
                    "entries": entries_sorted,
                    "entries_in_group": len(entries_sorted),
                    "auth_user_id": first.get("auth_user_id"),
                    "user_name": first.get("user_name"),
                    "user_initials": first.get("user_initials"),
                    "task_id": first.get("task_id"),
                    "task_name": first.get("task_name"),
                    "description": first.get("description"),
                    "rounded_hours": first.get("rounded_hours"),
                    "billable_amount": first.get("billable_amount"),
                    "currency": first.get("currency"),
                }
            )
    return out


def _renumber_duplicate_group_labels(groups: list[dict[str, Any]]) -> None:
    for idx, group in enumerate(groups, start=1):
        group["group_label"] = f"DUP-{idx:04d}"


async def find_duplicate_time_entries_for_project(
    session: AsyncSession,
    *,
    project_id: str,
    project_currency: str,
    date_from: date | None = None,
    date_to: date | None = None,
    users_map: dict[int, TimeTrackingUserModel] | None = None,
    tasks_map: dict[str, TimeManagerClientTaskModel] | None = None,
    initials_map: dict[int, str | None] | None = None,
    min_group_size: int = 2,
) -> dict[str, Any]:
    repo = TimeEntryRepository(session)
    entries = await repo.list_entries_for_project(project_id, date_from, date_to)
    if not entries:
        return {"groups": [], "summary": {"group_count": 0, "entry_count": 0, "user_count": 0}}

    if users_map is None:
        from sqlalchemy import select

        users_map = {
            u.auth_user_id: u
            for u in (await session.execute(select(TimeTrackingUserModel))).scalars().all()
        }
    if tasks_map is None:
        from sqlalchemy import select

        tasks_map = {
            t.id: t
            for t in (await session.execute(select(TimeManagerClientTaskModel))).scalars().all()
        }

    user_ids = sorted({e.auth_user_id for e in entries})
    rates_map = await _load_user_rates(session, user_ids)
    cur = (project_currency or "USD").strip()[:10] or "USD"

    groups: dict[DuplicateKey, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        u = users_map.get(e.auth_user_id)
        t = tasks_map.get(e.task_id) if e.task_id else None
        key, row = _entry_to_duplicate_row(
            e,
            u=u,
            t=t,
            project_currency=cur,
            rates_map=rates_map,
            initials_map=initials_map,
        )
        groups[key].append(row)

    # Merge near-duplicate notes (glued task prefix vs clean note, truncated note, etc.)
    meta_buckets: dict[tuple, list[tuple[DuplicateKey, list[dict[str, Any]]]]] = defaultdict(list)
    for dk, rows in groups.items():
        meta_buckets[_duplicate_meta_key(dk)].append((dk, rows))
    groups = {}
    for items in meta_buckets.values():
        clusters: list[tuple[DuplicateKey, list[dict[str, Any]]]] = []
        for dk, rows in items:
            placed = False
            for i, (cdk, crows) in enumerate(clusters):
                if notes_are_near_duplicate(dk.note_norm, cdk.note_norm):
                    key_dk = cdk if len(cdk.note_norm) >= len(dk.note_norm) else dk
                    clusters[i] = (key_dk, crows + rows)
                    placed = True
                    break
            if not placed:
                clusters.append((dk, list(rows)))
        for dk, rows in clusters:
            groups[dk] = rows

    dup_groups = [g for g in groups.values() if len(g) >= min_group_size]
    dup_groups.sort(
        key=lambda rows: (
            -len(rows),
            rows[0].get("user_name") or "",
            rows[0].get("work_date") or "",
        )
    )

    out_groups: list[dict[str, Any]] = []
    for idx, rows in enumerate(dup_groups, start=1):
        first = rows[0]
        key = DuplicateKey(
            auth_user_id=int(first["auth_user_id"]),
            work_date=date.fromisoformat(str(first["work_date"])),
            task_id=(first.get("task_id") or "").strip(),
            note_norm=_norm_note(first.get("description"), first.get("task_name")),
            hours_key=_hours_key(_d(first.get("rounded_hours"))),
            amount_key=_money_key(_d(first.get("billable_amount"))),
            currency=str(first.get("currency") or cur),
        )
        rows_sorted = sorted(rows, key=lambda r: (r.get("created_at") or "", r.get("entry_id") or ""))
        out_groups.append(
            {
                "group_id": key.as_group_id(),
                "group_label": f"DUP-{idx:04d}",
                "auth_user_id": key.auth_user_id,
                "user_name": first.get("user_name"),
                "user_initials": first.get("user_initials"),
                "work_date": first.get("work_date"),
                "task_id": first.get("task_id"),
                "task_name": first.get("task_name"),
                "description": first.get("description"),
                "rounded_hours": first.get("rounded_hours"),
                "billable_amount": first.get("billable_amount"),
                "currency": first.get("currency"),
                "entries_in_group": len(rows_sorted),
                "entries": rows_sorted,
            }
        )

    out_groups = split_duplicate_groups_by_work_date(out_groups, min_group_size=min_group_size)
    _renumber_duplicate_group_labels(out_groups)

    entry_count = sum(g["entries_in_group"] for g in out_groups)
    user_count = len({g["auth_user_id"] for g in out_groups})
    return {
        "groups": out_groups,
        "summary": {
            "group_count": len(out_groups),
            "entry_count": entry_count,
            "user_count": user_count,
        },
    }
