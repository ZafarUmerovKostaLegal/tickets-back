from __future__ import annotations

from datetime import date
from decimal import Decimal

from scripts.import_harvest_time_report import (
    HarvestRow,
    _harvest_user_rate_intervals,
    _harvest_users_for_project,
    _parse_hours,
    _parse_money_rate,
    _user_needs_billable_rate_from_csv,
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
