from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from application.services.reports._base import (
    _d,
    _hours,
    _money,
    _percent_billable,
    build_response,
    canonical_tt_project_id,
    project_ids_for_clients_norm,
)


def test_project_ids_for_clients_norm():
    projects = {
        "P1": SimpleNamespace(client_id="C1"),
        "P2": SimpleNamespace(client_id="c2"),
        "P3": SimpleNamespace(client_id=None),
    }
    assert project_ids_for_clients_norm(projects, ["C1", "C2"]) == {"p1", "p2"}


def test_canonical_tt_project_id_case():
    projects = {"AbC": SimpleNamespace()}
    assert canonical_tt_project_id("abc", projects) == "AbC"
    assert canonical_tt_project_id(" unknown ", projects) == "unknown"
    assert canonical_tt_project_id(None, projects) is None
    assert canonical_tt_project_id("  ", projects) is None


def test_decimal_helpers():
    assert _d(None) == Decimal(0)
    assert _d(Decimal("1.5")) == Decimal("1.5")
    assert _hours(Decimal("1.2345678")) == 1.234568
    assert _money(Decimal("10.006")) == 10.01


def test_percent_billable():
    assert _percent_billable(Decimal(0), Decimal(5)) == 0.0
    assert _percent_billable(Decimal(10), Decimal(5)) == 50.0


def test_build_response_pagination():
    out = build_response(
        results=[{"a": 1}],
        total_entries=25,
        page=2,
        per_page=10,
        report_type="time",
        group_by="projects",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
    )
    assert out["pagination"]["total_pages"] == 3
    assert out["pagination"]["next_page"] == 3
    assert out["pagination"]["previous_page"] == 1
    assert out["meta"]["from"] == "2026-01-01"
    assert out["meta"]["report_type"] == "time"


def test_build_response_empty_total_pages():
    out = build_response(
        results=[],
        total_entries=0,
        page=1,
        per_page=50,
        report_type="budget",
        group_by=None,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 2),
    )
    assert out["pagination"]["total_pages"] == 1
    assert out["pagination"]["next_page"] is None
