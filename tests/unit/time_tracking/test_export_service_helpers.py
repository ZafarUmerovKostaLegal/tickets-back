from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from application.services.reports.export_service import (
    _cell_value_for_export,
    _collect_fieldnames,
    _filename,
    _json_default,
    _row_to_plain_dict,
)


def test_filename_with_group():
    assert (
        _filename("time", "projects", date(2026, 1, 1), date(2026, 1, 31), "csv")
        == "time_projects_2026-01-01_2026-01-31.csv"
    )


def test_filename_without_group():
    assert (
        _filename("budget", None, date(2026, 2, 1), date(2026, 2, 28), "json")
        == "budget_2026-02-01_2026-02-28.json"
    )


def test_collect_fieldnames_preserves_order():
    rows = [{"b": 1, "a": 2}, {"a": 3, "c": 4}]
    assert _collect_fieldnames(rows) == ["b", "a", "c"]


def test_json_default_and_cells():
    assert _json_default(date(2026, 3, 1)) == "2026-03-01"
    assert _json_default(Decimal("1.5")) == 1.5
    assert _cell_value_for_export(None) == ""
    assert _cell_value_for_export(True) is True
    assert _cell_value_for_export(Decimal("2")) == 2.0
    assert _cell_value_for_export({"x": 1}) == '{"x": 1}'


def test_row_to_plain_dict():
    assert _row_to_plain_dict({"a": 1}) == {"a": 1}
    row = SimpleNamespace(model_dump=lambda mode=None: {"id": 1})
    assert _row_to_plain_dict(row) == {"id": 1}
