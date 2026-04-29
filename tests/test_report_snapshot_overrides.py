

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from application.report_snapshot_overrides import (
    OVERRIDE_ALLOWED_KEYS,
    merge_frozen_and_overrides,
    validate_and_normalize_overrides,
)


def test_allows_note_and_hours_camel() -> None:
    out = validate_and_normalize_overrides({"note": "x", "hours": 2.5})
    assert out["note"] == "x"
    assert out["hours"] == 2.5


def test_normalizes_snake_case() -> None:
    out = validate_and_normalize_overrides({"work_date": "2026-01-15", "client_name": "ACME"})
    assert out["workDate"] == "2026-01-15"
    assert out["clientName"] == "ACME"


def test_rejects_project_code() -> None:
    with pytest.raises(ValueError, match="код проекта|нельзя"):
        validate_and_normalize_overrides({"projectCode": "X-1"})


def test_rejects_time_entry_id() -> None:
    with pytest.raises(ValueError, match="нельзя"):
        validate_and_normalize_overrides({"timeEntryId": "uuid"})


def test_merge() -> None:
    m = merge_frozen_and_overrides(
        {"projectCode": "P1", "projectName": "A", "note": "old"},
        {"note": "new"},
    )
    assert m["projectCode"] == "P1"
    assert m["projectName"] == "A"
    assert m["note"] == "new"


def test_allowlist_covers_intended_user_fields() -> None:

    assert "projectCode" not in OVERRIDE_ALLOWED_KEYS
    assert "timeEntryId" not in OVERRIDE_ALLOWED_KEYS
    assert "note" in OVERRIDE_ALLOWED_KEYS
