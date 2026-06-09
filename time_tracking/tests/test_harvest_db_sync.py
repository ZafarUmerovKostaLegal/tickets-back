from __future__ import annotations

from datetime import date
from decimal import Decimal

from scripts.import_harvest_time_report import (
    HarvestRow,
    ProjectImportExpectation,
    _build_csv_expectations_map,
    _expectation_from_project_rows,
    _project_db_matches_expectation,
    _project_key,
)


def _sample_row(
    client: str,
    project: str,
    n: int,
    *,
    hours: str = "1.5",
    billable: bool = True,
) -> HarvestRow:
    return HarvestRow(
        source_row_number=n,
        work_date=date(2024, 1, 1),
        client_name=client,
        project_name=project,
        project_code=None,
        task_name="Task",
        notes=None,
        hours=Decimal(hours),
        is_billable=billable,
        first_name="A",
        last_name="B",
        employee_id=None,
        billable_rate=Decimal("100"),
        cost_rate=Decimal("0"),
        currency="EUR",
        external_reference_url=None,
    )


def test_expectation_from_project_rows() -> None:
    rows = [
        _sample_row("C", "P", 10, hours="2.00", billable=True),
        _sample_row("C", "P", 11, hours="1.00", billable=False),
    ]
    exp = _expectation_from_project_rows(rows, "report.csv")
    assert exp.total == Decimal("3.00")
    assert exp.billable == Decimal("2.00")
    assert exp.non_billable == Decimal("1.00")
    assert exp.row_count == 2
    assert exp.refs == frozenset(
        {
            "harvest-import:report.csv:10",
            "harvest-import:report.csv:11",
        }
    )


def test_build_csv_expectations_map() -> None:
    rows = [
        _sample_row("Alpha", "One", 1),
        _sample_row("Alpha", "Two", 2),
    ]
    m = _build_csv_expectations_map(rows, "file.csv")
    assert set(m.keys()) == {
        _project_key("Alpha", "One"),
        _project_key("Alpha", "Two"),
    }


def test_project_db_matches_expectation() -> None:
    expected = ProjectImportExpectation(
        total=Decimal("3.00"),
        billable=Decimal("2.00"),
        non_billable=Decimal("1.00"),
        row_count=2,
        refs=frozenset({"harvest-import:report.csv:10", "harvest-import:report.csv:11"}),
    )
    assert _project_db_matches_expectation(
        expected,
        db_total=Decimal("3.00"),
        db_billable=Decimal("2.00"),
        db_non_billable=Decimal("1.00"),
        db_row_count=2,
        db_refs={"harvest-import:report.csv:10", "harvest-import:report.csv:11"},
    )
    assert not _project_db_matches_expectation(
        expected,
        db_total=Decimal("3.00"),
        db_billable=Decimal("2.00"),
        db_non_billable=Decimal("1.00"),
        db_row_count=2,
        db_refs={"harvest-import:report.csv:10"},
    )
