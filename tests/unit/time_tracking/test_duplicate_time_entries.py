from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from application.duplicate_time_entries import (
    DuplicateKey,
    build_duplicate_key_for_entry,
    deduplicate_entries_for_report,
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


def _model(
    entry_id: str,
    *,
    work_date: date,
    created_at: datetime,
    project_id: str = "p1",
    description: str = "Same note",
    hours: str = "1.233333",
    rounded_hours: str = "1.233333",
    task_id: str = "task-1",
    auth_user_id: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=entry_id,
        auth_user_id=auth_user_id,
        work_date=work_date,
        created_at=created_at,
        project_id=project_id,
        task_id=task_id,
        description=description,
        hours=Decimal(hours),
        rounded_hours=Decimal(rounded_hours),
        is_billable=True,
        voided_at=None,
    )


def test_duplicate_key_ignores_created_date():
    k1 = DuplicateKey(
        auth_user_id=1,
        work_date=date(2025, 10, 28),
        task_id="t1",
        note_norm="note",
        hours_key="1.0",
        amount_key="0.00",
        currency="USD",
    )
    k2 = DuplicateKey(
        auth_user_id=1,
        work_date=date(2025, 10, 28),
        task_id="t1",
        note_norm="note",
        hours_key="1.0",
        amount_key="0.00",
        currency="USD",
    )
    assert k1.as_group_id() == k2.as_group_id()


def test_split_mixed_created_dates_stays_one_group():
    mixed = _group([
        _entry("a", "2025-10-28", "2026-06-09T16:09:18Z"),
        _entry("b", "2025-10-28", "2026-06-19T13:42:10Z"),
        _entry("c", "2025-10-28", "2026-06-19T16:40:12Z"),
    ])
    out = split_duplicate_groups_by_work_date([mixed])
    assert len(out) == 1
    assert len(out[0]["entries"]) == 3


def test_split_keeps_same_work_date_group():
    same_day = _group([
        _entry("a", "2025-10-28", "2026-06-19T10:00:00Z"),
        _entry("b", "2025-10-28", "2026-06-19T11:00:00Z"),
        _entry("c", "2025-10-28", "2026-06-19T12:00:00Z"),
    ])
    out = split_duplicate_groups_by_work_date([same_day])
    assert len(out) == 1
    assert len(out[0]["entries"]) == 3


def test_split_drops_singleton_after_mixed_work_date_split():
    mixed = _group([
        _entry("only", "2025-10-28", "2026-06-09T10:00:00Z"),
        _entry("b", "2026-06-19", "2026-06-19T13:42:10Z"),
    ])
    out = split_duplicate_groups_by_work_date([mixed])
    assert out == []


def test_deduplicate_entries_for_report_keeps_earliest():
    created = datetime(2026, 2, 12, 13, 42, tzinfo=timezone.utc)
    entries = [
        _model("b", work_date=date(2026, 2, 12), created_at=created.replace(hour=16)),
        _model("a", work_date=date(2026, 2, 12), created_at=created),
    ]
    projects_map = {"p1": SimpleNamespace(currency="USD")}
    kept, dropped = deduplicate_entries_for_report(entries, projects_map=projects_map, rates_map={1: []})
    assert dropped == 1
    assert [e.id for e in kept] == ["a"]


def test_deduplicate_entries_for_report_treats_different_record_time_as_duplicate():
    entries = [
        _model(
            "a",
            work_date=date(2026, 2, 13),
            created_at=datetime(2026, 2, 13, 13, 42, tzinfo=timezone.utc),
        ),
        _model(
            "b",
            work_date=date(2026, 2, 13),
            created_at=datetime(2026, 2, 13, 16, 11, tzinfo=timezone.utc),
        ),
    ]
    projects_map = {"p1": SimpleNamespace(currency="USD")}
    kept, dropped = deduplicate_entries_for_report(entries, projects_map=projects_map, rates_map={1: []})
    assert dropped == 1
    assert [e.id for e in kept] == ["a"]


def test_build_duplicate_key_for_entry_uses_note_normalization():
    e = _model("x", work_date=date(2026, 1, 1), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description="  Same   Note ")
    key = build_duplicate_key_for_entry(e, project_currency="USD", rates_map={1: []})
    assert key.note_norm == "same note"
