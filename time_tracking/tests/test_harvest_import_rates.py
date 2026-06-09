from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from scripts.import_harvest_time_report import (
    HarvestRow,
    HarvestProjectCatalogEntry,
    _build_harvest_project_archived_map,
    _harvest_user_rate_intervals,
    _harvest_project_is_archived,
    _harvest_users_for_project,
    _normalize_harvest_project_status,
    _parse_hours,
    _parse_money_rate,
    _project_key,
    _user_has_billable_rate_for_harvest_rows,
    _user_needs_billable_rate_from_csv,
    _write_projects_catalog_from_time_rows,
)


def _row(
    work_date: date,
    rate: str,
    *,
    billable: bool = True,
    client: str = "EVYAP",
    project: str = "Company Establishment",
) -> HarvestRow:
    return HarvestRow(
        source_row_number=1,
        work_date=work_date,
        client_name=client,
        project_name=project,
        project_code=None,
        task_name="Task",
        notes=None,
        hours=Decimal("1"),
        is_billable=billable,
        first_name="Aliye",
        last_name="Ablyalimova",
        employee_id=None,
        billable_rate=Decimal(rate),
        cost_rate=Decimal("0"),
        currency="EUR",
        external_reference_url=None,
    )


def test_rate_intervals_single_amount() -> None:
    rows = [
        _row(date(2023, 1, 23), "120"),
        _row(date(2023, 1, 24), "120"),
        _row(date(2023, 1, 26), "120"),
    ]
    assert _harvest_user_rate_intervals(rows) == [
        (Decimal("120"), "EUR", date(2023, 1, 23), date(2023, 1, 26)),
    ]


def test_rate_intervals_rate_change() -> None:
    rows = [
        _row(date(2023, 1, 23), "120"),
        _row(date(2023, 2, 10), "120"),
        _row(date(2023, 2, 23), "180"),
        _row(date(2023, 3, 1), "180"),
    ]
    assert _harvest_user_rate_intervals(rows) == [
        (Decimal("120"), "EUR", date(2023, 1, 23), date(2023, 2, 10)),
        (Decimal("180"), "EUR", date(2023, 2, 23), date(2023, 3, 1)),
    ]


def test_rate_intervals_skips_non_billable_zero() -> None:
    rows = [
        _row(date(2024, 6, 5), "0", billable=False),
        _row(date(2024, 6, 6), "120"),
    ]
    assert _harvest_user_rate_intervals(rows) == [
        (Decimal("120"), "EUR", date(2024, 6, 6), date(2024, 6, 6)),
    ]


def test_users_for_project_unique() -> None:
    rows = [
        _row(date(2023, 1, 1), "120", client="C1", project="P1"),
        _row(date(2023, 1, 2), "120", client="C1", project="P1"),
        _row(date(2023, 1, 1), "100", client="C1", project="P2"),
    ]
    team = _harvest_users_for_project(rows, "C1", "P1")
    assert len(team) == 1
    assert "aliye ablyalimova" in team


def test_needs_billable_rate_only_when_billable_hours() -> None:
    assert _user_needs_billable_rate_from_csv([_row(date(2023, 1, 1), "120")])
    assert not _user_needs_billable_rate_from_csv([_row(date(2023, 1, 1), "0", billable=False)])


def test_parse_money_rate_handles_thousands_separator() -> None:
    # UZS-отчёты пишут ставку как "2,300,000.0"
    assert _parse_money_rate("2,300,000.0") == Decimal("2300000.0000")
    assert _parse_money_rate("120.0") == Decimal("120.0000")
    assert _parse_money_rate("") is None
    assert _parse_money_rate(None) is None


def test_parse_hours_handles_thousands_separator() -> None:
    assert _parse_hours("0.17") == Decimal("0.17")
    assert _parse_hours("1,234.5") == Decimal("1234.50")
    assert _parse_hours("") is None


def test_billable_rate_check_uses_harvest_work_dates_not_today() -> None:
    from dataclasses import dataclass

    @dataclass
    class FakeRate:
        valid_from: date
        valid_to: date | None
        currency: str = "USD"
        id: str = "1"

    rows = [_row(date(2018, 5, 10), "220", client="24 FLOOR", project="Strategy")]
    rates = [FakeRate(valid_from=date(2018, 5, 10), valid_to=date(2018, 5, 12))]
    assert _user_has_billable_rate_for_harvest_rows(rows, rates, project_currency="USD")
    assert not _user_has_billable_rate_for_harvest_rows(
        rows, rates, project_currency="EUR"
    )


def test_normalize_harvest_project_status() -> None:
    assert _normalize_harvest_project_status("Archived projects (602)") == "archived"
    assert _normalize_harvest_project_status("Active projects (169)") == "active"
    assert _normalize_harvest_project_status("Budgeted projects (125)") == "budgeted"
    assert _normalize_harvest_project_status("На паузе") == "paused"
    assert _normalize_harvest_project_status("") is None


def test_harvest_project_is_archived() -> None:
    assert _harvest_project_is_archived("Archived projects") is True
    assert _harvest_project_is_archived("Active projects") is False
    assert _harvest_project_is_archived("Budgeted projects") is False
    assert _harvest_project_is_archived(None) is None


def test_build_harvest_project_archived_map() -> None:
    catalog = [
        HarvestProjectCatalogEntry(
            client_name="A",
            project_name="P1",
            project_code=None,
            currency="USD",
            status="archived",
            is_archived=True,
        ),
        HarvestProjectCatalogEntry(
            client_name="A",
            project_name="P2",
            project_code=None,
            currency="USD",
            status=None,
            is_archived=None,
        ),
        HarvestProjectCatalogEntry(
            client_name="B",
            project_name="P3",
            project_code=None,
            currency="EUR",
            status="active",
            is_archived=False,
        ),
    ]
    out = _build_harvest_project_archived_map(catalog)
    assert out[_project_key("A", "P1")] is True
    assert out[_project_key("B", "P3")] is False
    assert _project_key("A", "P2") not in out


def test_write_projects_catalog_from_time_rows(tmp_path: Path) -> None:
    out_file = tmp_path / "harvest_projects_all.csv"
    rows = [
        _row(date(2024, 1, 10), "220", client="24 FLOOR", project="Strategy"),
        _row(date(2024, 1, 11), "220", client="24 FLOOR", project="Strategy"),
    ]
    generated = _write_projects_catalog_from_time_rows(rows, out_file)
    text = out_file.read_text(encoding="utf-8")
    assert generated == 1
    assert "Client,Project,Project Code,Currency,Project Status" in text
    assert "24 FLOOR,Strategy,,EUR," in text
