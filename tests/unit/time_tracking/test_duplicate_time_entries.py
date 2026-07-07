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
        "group_id": "user|2024-01-05|task|note|4.27|0|USD",
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


def test_duplicate_key_includes_work_date():
    k9 = DuplicateKey(
        auth_user_id=1,
        work_date=date(2026, 6, 9),
        task_id="t1",
        note_norm="note",
        hours_key="1.0",
        amount_key="0.00",
        currency="USD",
    )
    k19 = DuplicateKey(
        auth_user_id=1,
        work_date=date(2026, 6, 19),
        task_id="t1",
        note_norm="note",
        hours_key="1.0",
        amount_key="0.00",
        currency="USD",
    )
    assert k9.as_group_id() != k19.as_group_id()


def test_split_mixed_work_dates_keeps_only_same_day_pairs():
    mixed = _group([
        _entry("a", "2026-06-09", "2026-06-09T10:00:00Z"),
        _entry("b", "2026-06-19", "2026-06-19T13:41:50Z"),
        _entry("c", "2026-06-19", "2026-06-19T16:39:51Z"),
    ])
    out = split_duplicate_groups_by_work_date([mixed])
    assert len(out) == 1
    assert out[0]["work_date"] == "2026-06-19"
    assert [e["entry_id"] for e in out[0]["entries"]] == ["b", "c"]


def test_split_keeps_single_work_date_group():
    same_day = _group([
        _entry("a", "2024-01-05", "2026-06-09T10:00:00Z"),
        _entry("b", "2024-01-05", "2026-06-19T13:41:50Z"),
        _entry("c", "2024-01-05", "2026-06-19T16:39:51Z"),
    ])
    out = split_duplicate_groups_by_work_date([same_day])
    assert len(out) == 1
    assert out[0]["work_date"] == "2024-01-05"
    assert len(out[0]["entries"]) == 3


def test_split_drops_singleton_after_mixed_split():
    mixed = _group([
        _entry("only", "2026-06-09", "2026-06-09T10:00:00Z"),
        _entry("b", "2026-06-19", "2026-06-19T13:41:50Z"),
    ])
    out = split_duplicate_groups_by_work_date([mixed])
    assert out == []
