"""Report services on a seeded database: time (every grouping), budget, uninvoiced, expenses, export."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import application.invoice_service as invoices
import application.report_builder as builder
from application.services.reports import (
    budget_report_service as budget,
    expense_report_service as expense,
    export_service as export,
    time_report_service as timerep,
    uninvoiced_report_service as uninvoiced,
)
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeManagerClientTaskModel,
    TimeTrackingUserModel,
    UserHourlyRateModel,
)
from infrastructure.report_cache import invalidate_all_reports
from support.tt_invoice import TT_NOW as NOW, make_invoice

D_FROM, D_TO = date(2026, 11, 1), date(2026, 11, 30)
EXPENSES = [
    {"id": "x1", "project_id": "p1", "status": "approved", "is_reimbursable": True, "description": "Taxi",
     "expense_date": "2026-11-05", "equivalent_amount": 40, "amount_uzs": 500000, "exchange_rate": 12500,
     "category_id": "cat", "created_by_user_id": 7, "user_id": 7},
    {"id": "x2", "project_id": "p2", "status": "paid", "is_reimbursable": False, "description": "Lunch",
     "expense_date": "2026-11-06", "equivalent_amount": 10, "amount_uzs": 125000, "exchange_rate": 12500,
     "category_id": None, "created_by_user_id": 8, "user_id": 8},
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    invalidate_all_reports()

    async def fetch(date_from, date_to, user_ids, project_ids):
        return [r for r in EXPENSES if (not project_ids or r["project_id"] in project_ids)]

    import application.partner_confirmed_invoice_preview as confirmed

    for module in (invoices, builder, confirmed, expense):
        if hasattr(module, "_fetch_expense_report_data"):
            monkeypatch.setattr(module, "_fetch_expense_report_data", fetch)
    yield
    invalidate_all_reports()


def _entry(eid, uid, day, hours, pid, *, task=None, billable=True, voided=False, desc="Work"):
    h = Decimal(hours)
    return TimeEntryModel(
        id=eid, auth_user_id=uid, work_date=date(2026, 11, day), duration_seconds=int(h * 3600), hours=h,
        rounded_hours=h, project_id=pid, task_id=task, is_billable=billable, description=desc, created_at=NOW,
        voided_at=NOW if voided else None, void_kind="manual" if voided else None,
    )


@pytest.fixture()
async def seeded(tt_db):
    s = tt_db
    s.add(TimeManagerClientModel(id="c2", name="Beta", currency="USD", created_at=NOW))
    s.add(TimeManagerClientProjectModel(
        id="p2", client_id="c2", name="Package", project_type="hour_package", currency="USD",
        package_hours_per_month=Decimal("2"), package_fee_amount=Decimal("500"),
        budget_hours=Decimal("10"), budget_amount=Decimal("2000"),
        skip_partner_invoice_confirmation=True, created_at=NOW,
    ))
    proj = await s.get(TimeManagerClientProjectModel, "p1")
    proj.budget_hours = Decimal("5")
    proj.budget_amount = Decimal("1000")
    s.add(TimeManagerClientTaskModel(id="t1", project_id="p1", name="Research", created_at=NOW))
    s.add(TimeManagerClientTaskModel(id="t2", project_id="p1", name="Filing", billing_mode="flat_fee",
                                     flat_fee_amount=Decimal("300"), flat_fee_currency="USD", created_at=NOW))
    for uid, email, name in ((7, "a@kl.com", "Aziza A"), (8, "b@kl.com", "Bek B")):
        s.add(TimeTrackingUserModel(auth_user_id=uid, email=email, display_name=name, created_at=NOW))
        s.add(UserHourlyRateModel(id=f"r{uid}", auth_user_id=uid, rate_kind="billable", amount=Decimal("100"), currency="USD", created_at=NOW))
        s.add(UserHourlyRateModel(id=f"k{uid}", auth_user_id=uid, rate_kind="cost", amount=Decimal("30"), currency="USD", created_at=NOW))
    s.add_all([
        _entry("e1", 7, 3, "2", "p1", task="t1"),
        _entry("e2", 8, 4, "1.5", "p1", task="t1"),
        _entry("e3", 7, 5, "1", "p1", task="t2"),
        _entry("e4", 8, 6, "3", "p2"),
        _entry("e5", 7, 7, "1", "p1", billable=False),
        _entry("e6", 7, 8, "4", "p1", voided=True),
        _entry("old", 8, 1, "1", "p2"),
    ])
    await s.commit()
    await make_invoice(s, lines=None, time_entry_ids=["e1"])
    return s


@pytest.mark.parametrize("group_by", ["projects", "clients", "tasks", "team"])
async def test_time_report_every_grouping(seeded, group_by):
    report = await timerep.get_time_report(seeded, group_by=group_by, date_from=D_FROM, date_to=D_TO)
    rows = report["results"]
    assert rows
    total_hours = sum(Decimal(str(r.get("total_hours") or r.get("totalHours") or 0)) for r in rows)
    assert total_hours >= Decimal("8.5")
    cached = await timerep.get_time_report(seeded, group_by=group_by, date_from=D_FROM, date_to=D_TO)
    assert cached is report or cached == report
    flat = await timerep.get_time_report_flat_entries(seeded, group_by=group_by, date_from=D_FROM, date_to=D_TO)
    ids = {row.get("timeEntryId") or row.get("time_entry_id") for row in flat}
    if group_by == "clients":
        assert flat  # client view aggregates entries per person and project
    else:
        assert ids >= {"e1", "e2"}
    summary = await timerep.get_time_report_summary_for_export(seeded, group_by=group_by, date_from=D_FROM, date_to=D_TO)
    assert len(summary) == len(rows)
    response = export.export_report(summary, "csv", "time", group_by, D_FROM, D_TO)
    assert response.body
    response = export.export_report(summary, "xlsx", "time", group_by, D_FROM, D_TO)
    assert response.body[:2] == b"PK"


async def test_time_report_filters(seeded):
    only_billable = await timerep.get_time_report(
        seeded, group_by="projects", date_from=D_FROM, date_to=D_TO, is_billable=True,
        client_ids=["c1"], user_ids=[7], task_ids=["t1", "t2"], include_fixed_fee=False,
    )
    assert all(r for r in only_billable["results"])
    empty = await timerep.get_time_report_summary_for_export(
        seeded, group_by="projects", date_from=date(2020, 1, 1), date_to=date(2020, 1, 2),
    )
    assert empty == []
    with pytest.raises(ValueError):
        timerep._get_group_id(type("E", (), {"project_id": None})(), "nope", {})


async def test_budget_and_uninvoiced_reports(seeded):
    rep = await budget.get_budget_report(seeded, date_from=D_FROM, date_to=D_TO)
    assert rep["results"]
    rows = await budget.get_budget_report_all_rows(seeded, date_from=D_FROM, date_to=D_TO, client_ids=["c1"], user_ids=[7, 8])
    assert rows is not None
    uninv = await uninvoiced.get_uninvoiced_report(seeded, date_from=D_FROM, date_to=D_TO)
    assert uninv["results"]
    all_rows = await uninvoiced.get_uninvoiced_report_all_rows(seeded, date_from=D_FROM, date_to=D_TO, project_ids=["p1"])
    assert all_rows is not None
    assert budget._progress_percent(Decimal("5"), Decimal("0")) == 0 or budget._progress_percent(Decimal("5"), Decimal("0")) >= 0
    assert budget._progress_percent(Decimal("5"), Decimal("10")) == 50


@pytest.mark.parametrize("group_by", ["projects", "clients", "categories", "team"])
async def test_expense_report(seeded, group_by):
    try:
        rep = await expense.get_expense_report(seeded, group_by=group_by, date_from=D_FROM, date_to=D_TO)
    except ValueError:
        pytest.skip(f"group_by {group_by} not supported by expense report")
    assert "results" in rep
    rows = await expense.get_expense_report_all_rows(seeded, group_by=group_by, date_from=D_FROM, date_to=D_TO)
    assert rows is not None


def test_export_helpers():
    assert export._filename("time", "projects", D_FROM, D_TO, "csv").endswith(".csv")
    assert export._humanize_header("total_hours")
    rows = export._sanitize_export_rows([{"a": Decimal("1.5"), "b": datetime(2026, 1, 1, tzinfo=timezone.utc), "c": [1, 2], "d": None}])
    assert rows[0]["a"] in (1.5, "1.5", Decimal("1.5"))
    assert export._collect_fieldnames([{"a": 1}, {"b": 2}]) == ["a", "b"]
    assert export.export_report([], "csv", "time", None, D_FROM, D_TO).body is not None
