from __future__ import annotations

from datetime import date
from decimal import Decimal

from scripts.import_harvest_time_report import (
    HarvestRow,
    _harvest_user_rate_intervals,
    _user_needs_harvest_import_rates,
    HARVEST_IMPORT_AUTH_ID_FLOOR,
)


def _row(work_date: date, rate: str, *, billable: bool = True) -> HarvestRow:
    return HarvestRow(
        source_row_number=1,
        work_date=work_date,
        client_name="C",
        project_name="P",
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


def test_user_needs_rates_for_harvest_and_archived() -> None:
    assert _user_needs_harvest_import_rates(
        auth_user_id=HARVEST_IMPORT_AUTH_ID_FLOOR,
        user_source="harvest",
        is_tt_archived=True,
        auth_is_archived=None,
    )
    assert _user_needs_harvest_import_rates(
        auth_user_id=1,
        user_source="auth",
        is_tt_archived=True,
        auth_is_archived=False,
    )
    assert not _user_needs_harvest_import_rates(
        auth_user_id=1,
        user_source="tt",
        is_tt_archived=False,
        auth_is_archived=False,
    )
