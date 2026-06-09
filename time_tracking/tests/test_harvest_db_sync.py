from __future__ import annotations

from datetime import date
from decimal import Decimal

from scripts.import_harvest_time_report import (
    HarvestProjectCatalogEntry,
    HarvestRow,
    ProjectImportExpectation,
    _build_csv_expectations_map,
    _build_harvest_meta_maps,
    _expectation_from_project_rows,
    _merge_project_pairs,
    _norm_project_code,
    _project_db_matches_expectation,
    _project_key,
    _resolve_harvest_project_code,
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


def test_norm_project_code() -> None:
    assert _norm_project_code("NBS-10") == "nbs-10"
    assert _norm_project_code("  ") is None
    assert _norm_project_code(None) is None


def test_build_csv_expectations_map_extra_pairs() -> None:
    rows = [_sample_row("Alpha", "One", 1)]
    m = _build_csv_expectations_map(
        rows,
        "file.csv",
        extra_project_pairs=[("Beta", "Empty")],
    )
    assert len(m) == 2
    empty = m[_project_key("Beta", "Empty")]
    assert empty.row_count == 0
    assert empty.total == Decimal("0")


def test_merge_project_pairs_and_meta() -> None:
    rows = [
        HarvestRow(
            source_row_number=1,
            work_date=date(2024, 1, 1),
            client_name="C1",
            project_name="P1",
            project_code="CODE1",
            task_name="Task",
            notes=None,
            hours=Decimal("2"),
            is_billable=True,
            first_name="A",
            last_name="B",
            employee_id=None,
            billable_rate=Decimal("100"),
            cost_rate=Decimal("0"),
            currency="UZS",
            external_reference_url=None,
        )
    ]
    catalog = [
        HarvestProjectCatalogEntry("C1", "P2", "CODE2", "USD"),
    ]
    pairs = _merge_project_pairs(rows, catalog)
    assert pairs == [("C1", "P1"), ("C1", "P2")]
    client_cur, proj_cur, codes = _build_harvest_meta_maps(rows, catalog)
    assert client_cur["c1"] == "UZS"
    assert proj_cur[_project_key("C1", "P2")] == "USD"
    assert codes[_project_key("C1", "P1")] == "CODE1"


def test_resolve_harvest_project_code_conflict() -> None:
    assert _resolve_harvest_project_code(
        "NBS-10",
        existing_code_owner_name=None,
        new_project_name="PIF Transfer",
    ) == "NBS-10"
    assert _resolve_harvest_project_code(
        "NBS-10",
        existing_code_owner_name="Land return, cadaster and mortgage",
        new_project_name="PIF Transfer",
    ) is None
    assert _resolve_harvest_project_code(
        "NBS-10",
        existing_code_owner_name="PIF Transfer",
        new_project_name="PIF Transfer",
    ) == "NBS-10"
