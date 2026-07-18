"""Unit tests for building invoice lines strictly from a confirmed report snapshot."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from support.service_path import ensure_service_in_path


class _Row:
    """Minimal stand-in for ReportSnapshotRowModel (only fields used by the helpers)."""

    def __init__(
        self,
        *,
        source_type: str = "time_entry",
        source_id: str = "",
        data: dict | None = None,
        overrides: dict | None = None,
        sort_order: int = 0,
    ) -> None:
        self.source_type = source_type
        self.source_id = source_id
        self.frozen_data_json = json.dumps(data or {})
        self.overrides_json = json.dumps(overrides) if overrides else None
        self.sort_order = sort_order


def _mod():
    ensure_service_in_path("time_tracking")
    import application.partner_snapshot_invoice_preview as m

    return m


def test_effective_data_override_wins():
    m = _mod()
    row = _Row(
        data={"billableRate": 100, "note": "orig"},
        overrides={"billableRate": 150, "note": "edited"},
    )
    d = m._effective_row_data(row)
    assert d["billableRate"] == 150
    assert d["note"] == "edited"


def test_round2_amount_matches_report_rule():
    m = _mod()
    # hours=2.63, rate=150 → 394.50 (как в подписанном отчёте)
    hours = m._round2(Decimal("2.63"))
    rate = m._round2(Decimal("150"))
    assert m._round2(hours * rate) == Decimal("394.50")


def test_included_billable_entry_row():
    m = _mod()
    row = _Row(
        source_type="time_entry",
        source_id="te-1",
        data={"timeEntryId": "te-1", "hours": 2, "billableRate": 100, "workDate": "2026-06-09"},
    )
    d = m._effective_row_data(row)
    assert m._is_included_billable_time_row(row, d) is True


def test_voided_row_excluded():
    m = _mod()
    row = _Row(data={"timeEntryId": "te-1", "hours": 2, "isVoided": True})
    d = m._effective_row_data(row)
    assert m._is_included_billable_time_row(row, d) is False


def test_zero_hours_row_excluded():
    m = _mod()
    row = _Row(data={"timeEntryId": "te-1", "hours": 0, "billableRate": 100})
    d = m._effective_row_data(row)
    assert m._is_included_billable_time_row(row, d) is False


def test_project_marker_row_excluded():
    m = _mod()
    # Минимальный снимок: строка-маркер проекта, не детализация.
    row = _Row(source_type="project", source_id="proj-1", data={"projectId": "proj-1"})
    d = m._effective_row_data(row)
    assert m._is_included_billable_time_row(row, d) is False


def test_aggregate_row_excluded():
    m = _mod()
    row = _Row(data={"rowKind": "aggregate", "hours": 10, "billableRate": 100})
    d = m._effective_row_data(row)
    assert m._is_included_billable_time_row(row, d) is False


def test_time_entry_id_falls_back_to_source_id():
    m = _mod()
    row = _Row(source_type="time_entry", source_id="te-src", data={"hours": 1})
    d = m._effective_row_data(row)
    assert m._row_time_entry_id(row, d) == "te-src"


def test_time_entry_id_prefers_explicit_field():
    m = _mod()
    row = _Row(source_type="time_entry", source_id="te-src", data={"timeEntryId": "te-explicit"})
    d = m._effective_row_data(row)
    assert m._row_time_entry_id(row, d) == "te-explicit"


def test_within_period_filters_out_of_range():
    m = _mod()
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    assert m._within_period("2026-06-15", df, dt) is True
    assert m._within_period("2026-07-01", df, dt) is False
    # без даты — включаем (нечем фильтровать)
    assert m._within_period("", df, dt) is True
