from datetime import date
from decimal import Decimal

from application.duplicate_time_entries import (
    DuplicateKey,
    split_duplicate_groups_by_work_date,
)


def _entry(entry_id: str, work_date: str, created_at: str) -> dict:
    return {
        "entry_id": entry_id,
        "auth_user_id": 1,
        "user_name": "User",
        "work_date": work_date,
        "task_id": "task-1",
        "task_name": "Task",
        "description": "Same note",
        "rounded_hours": 4.27,
        "billable_amount": 0.0,
        "currency": "USD",
        "created_at": created_at,
    }


def _group(entries: list[dict]) -> dict:
    first = entries[0]
    return {
        "group_id": "user|2024-01-05|2026-06-19|task|note|4.27|0|USD",
        "group_label": "DUP-0001",
        "auth_user_id": 1,
        "user_name": "User",
        "work_date": first["work_date"],
        "task_id": "task-1",
        "task_name": "Task",
        "description": "Same note",
        "rounded_hours": 4.27,
        "billable_amount": 0.0,
        "currency": "USD",
        "entries_in_group": len(entries),
        "entries": entries,
    }


def test_duplicate_key_includes_work_date_and_created_date():
    k9 = DuplicateKey(
        auth_user_id=1,
        work_date=date(2025, 10, 28),
        created_date=date(2026, 6, 9),
        task_id="t1",
        note_norm="note",
        hours_key="1.0",
        amount_key="0.00",
        currency="USD",
    )
    k19 = DuplicateKey(
        auth_user_id=1,
        work_date=date(2025, 10, 28),
        created_date=date(2026, 6, 19),
        task_id="t1",
        note_norm="note",
        hours_key="1.0",
        amount_key="0.00",
        currency="USD",
    )
    assert k9.as_group_id() != k19.as_group_id()


def test_split_mixed_created_dates_keeps_only_same_import_day():
    """Импорт 09.06 + два дубликата 19.06 при одном work_date → только пара 19.06."""
    mixed = _group([
        _entry("a", "2025-10-28", "2026-06-09T16:09:18Z"),
        _entry("b", "2025-10-28", "2026-06-19T13:42:10Z"),
        _entry("c", "2025-10-28", "2026-06-19T16:40:12Z"),
    ])
    out = split_duplicate_groups_by_work_date([mixed])
    assert len(out) == 1
    assert out[0]["work_date"] == "2025-10-28"
    assert [e["entry_id"] for e in out[0]["entries"]] == ["b", "c"]


def test_split_keeps_same_work_and_created_day_group():
    same_day = _group([
        _entry("a", "2025-10-28", "2026-06-19T10:00:00Z"),
        _entry("b", "2025-10-28", "2026-06-19T11:00:00Z"),
        _entry("c", "2025-10-28", "2026-06-19T12:00:00Z"),
    ])
    out = split_duplicate_groups_by_work_date([same_day])
    assert len(out) == 1
    assert len(out[0]["entries"]) == 3


def test_split_drops_singleton_after_mixed_split():
    mixed = _group([
        _entry("only", "2025-10-28", "2026-06-09T10:00:00Z"),
        _entry("b", "2025-10-28", "2026-06-19T13:42:10Z"),
    ])
    out = split_duplicate_groups_by_work_date([mixed])
    assert out == []
