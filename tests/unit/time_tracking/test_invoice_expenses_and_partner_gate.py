"""Invoice service: reimbursable expenses and the partner-confirmation gate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

import application.invoice_service as svc
from infrastructure.models import TimeEntryModel, TimeManagerClientProjectModel, UserHourlyRateModel
from infrastructure.repository_partner_report_confirmations import PartnerReportConfirmationRepository
from support.tt_invoice import TT_NOW as NOW, make_invoice

EXPENSES = [
    {"id": "x1", "project_id": "p1", "status": "approved", "is_reimbursable": True, "description": "Taxi",
     "expense_date": "2026-11-05", "equivalent_amount": "40", "amount_uzs": "500000", "exchange_rate": "12500"},
    {"id": "x2", "project_id": "p1", "status": "draft", "is_reimbursable": True, "description": "Hotel",
     "expense_date": "2026-11-06", "equivalent_amount": "200"},
    {"id": "x3", "project_id": "p1", "status": "paid", "is_reimbursable": False, "description": "Lunch",
     "expense_date": "2026-11-07", "equivalent_amount": "10"},
    {"id": "x4", "project_id": "p2", "status": "paid", "is_reimbursable": True, "description": "Courier",
     "expense_date": "2026-11-08", "equivalent_amount": "15"},
]


@pytest.fixture(autouse=True)
def fake_expenses(monkeypatch):
    async def fetch(date_from, date_to, _user, project_ids):
        return [row for row in EXPENSES if not project_ids or row["project_id"] in project_ids]

    import application.partner_confirmed_invoice_preview as confirmed
    import application.report_builder as builder

    for module in (svc, builder, confirmed):
        monkeypatch.setattr(module, "_fetch_expense_report_data", fetch)


async def _second_project(session):
    session.add(TimeManagerClientProjectModel(
        id="p2", client_id="c1", name="Eagle", project_type="time_and_materials", currency="USD",
        skip_partner_invoice_confirmation=True, created_at=NOW,
    ))
    await session.commit()


async def test_expense_lines_and_rules(tt_db):
    unbilled = await svc.list_unbilled_expenses(tt_db, project_id="p1", date_from=date(2026, 11, 1), date_to=date(2026, 11, 30))
    assert {row["id"] for row in unbilled} == {"x1", "x2"}

    inv = await make_invoice(tt_db, lines=None, expense_ids=["x1"])
    assert inv.subtotal == Decimal("40")
    lines = await svc._invoice_lines_for_totals(tt_db, inv)
    assert lines[0].source_currency == "UZS"
    unbilled = await svc.list_unbilled_expenses(tt_db, project_id="p1", date_from=date(2026, 11, 1), date_to=date(2026, 11, 30))
    assert {row["id"] for row in unbilled} == {"x2"}

    with pytest.raises(HTTPException, match="уже в счёте"):
        await make_invoice(tt_db, lines=None, expense_ids=["x1"])
    with pytest.raises(HTTPException, match="approved"):
        await make_invoice(tt_db, lines=None, expense_ids=["x2"])
    with pytest.raises(HTTPException, match="reimbursable"):
        await make_invoice(tt_db, lines=None, expense_ids=["x3"])
    with pytest.raises(HTTPException, match="не найден"):
        await make_invoice(tt_db, lines=None, expense_ids=["x4"])
    with pytest.raises(HTTPException):
        await make_invoice(tt_db, project_id=None, lines=None, expense_ids=["x1"])


async def test_expense_replace_line_on_draft(tt_db):
    inv = await make_invoice(tt_db)
    inv = await svc.patch_invoice_draft(
        tt_db, inv, actor_auth_user_id=1,
        replace_lines=[{"lineKind": "expense", "expenseRequestId": "x1"}, {"lineKind": "manual", "description": "Fee", "quantity": "1", "unitAmount": "10"}],
    )
    assert inv.subtotal == Decimal("50")
    with pytest.raises(HTTPException, match="не найден"):
        await svc.patch_invoice_draft(tt_db, inv, actor_auth_user_id=1, replace_lines=[{"lineKind": "expense", "expenseRequestId": "nope"}])


async def test_combined_invoice_across_projects(tt_db):
    await _second_project(tt_db)
    tt_db.add(UserHourlyRateModel(id="r1", auth_user_id=7, rate_kind="billable", amount=Decimal("100"), currency="USD", created_at=NOW))
    tt_db.add(TimeEntryModel(
        id="e1", auth_user_id=7, work_date=date(2026, 11, 3), duration_seconds=3600, hours=Decimal("1"),
        rounded_hours=Decimal("1"), project_id="p1", is_billable=True, created_at=NOW,
    ))
    await tt_db.commit()
    inv = await make_invoice(
        tt_db, project_id=None, lines=None, time_entry_ids=["e1"], expense_ids=["x1", "x4"], defer_partner_confirmation=True,
    )
    assert inv.subtotal == Decimal("155")


async def test_time_entry_rules(tt_db):
    await _second_project(tt_db)
    tt_db.add(UserHourlyRateModel(id="r1", auth_user_id=7, rate_kind="billable", amount=Decimal("100"), currency="USD", created_at=NOW))
    tt_db.add(TimeEntryModel(id="nb", auth_user_id=7, work_date=date(2026, 11, 3), duration_seconds=3600, hours=Decimal("1"),
                             rounded_hours=Decimal("1"), project_id="p1", is_billable=False, created_at=NOW))
    tt_db.add(TimeEntryModel(id="other", auth_user_id=7, work_date=date(2026, 11, 3), duration_seconds=3600, hours=Decimal("1"),
                             rounded_hours=Decimal("1"), project_id="p2", is_billable=True, created_at=NOW))
    await tt_db.commit()
    with pytest.raises(HTTPException, match="billable"):
        await make_invoice(tt_db, lines=None, time_entry_ids=["nb"])
    with pytest.raises(HTTPException, match="другому проекту"):
        await make_invoice(tt_db, lines=None, time_entry_ids=["other"])
    with pytest.raises(HTTPException, match="не найдена"):
        await make_invoice(tt_db, lines=None, time_entry_ids=["missing"])


async def _gate_project(session, *, confirmed: bool):
    proj = await session.get(TimeManagerClientProjectModel, "p1")
    proj.skip_partner_invoice_confirmation = False
    session.add(UserHourlyRateModel(id="r1", auth_user_id=7, rate_kind="billable", amount=Decimal("100"), currency="USD", created_at=NOW))
    for i in range(2):
        session.add(TimeEntryModel(
            id=f"g{i}", auth_user_id=7, work_date=date(2026, 11, 3 + i), duration_seconds=7200, hours=Decimal("2"),
            rounded_hours=Decimal("2"), project_id="p1", is_billable=True, created_at=NOW,
        ))
    repo = PartnerReportConfirmationRepository(session)
    req = await repo.upsert_submit(
        snapshot_id="s1", project_id="p1", date_from=date(2026, 11, 1), date_to=date(2026, 11, 30),
        title="November", submitted_by_auth_user_id=7,
    )
    if confirmed:
        await repo.mark_fully_confirmed(req.id)
    await session.commit()
    return req


async def test_partner_gate_requires_period_and_confirmation(tt_db):
    req = await _gate_project(tt_db, confirmed=False)
    with pytest.raises(HTTPException, match="partnerBillingPeriodFrom"):
        await make_invoice(tt_db, lines=None, time_entry_ids=["g0"])
    period = dict(partner_billing_period_from=date(2026, 11, 1), partner_billing_period_to=date(2026, 11, 30))
    with pytest.raises(HTTPException, match="раньше"):
        await make_invoice(tt_db, lines=None, time_entry_ids=["g0"],
                           partner_billing_period_from=date(2026, 11, 30), partner_billing_period_to=date(2026, 11, 1))
    with pytest.raises(HTTPException) as err:
        await make_invoice(tt_db, lines=None, time_entry_ids=["g0"], **period)
    assert err.value.status_code == 403
    with pytest.raises(HTTPException, match="projectId"):
        await make_invoice(tt_db, project_id=None, lines=None, **period)

    inv = await make_invoice(tt_db, lines=None, time_entry_ids=["g0", "g1"], defer_partner_confirmation=True, **period)
    assert inv.partner_confirmation_request_id == req.id
    assert "до подписей" in inv.internal_note
    assert inv.subtotal == Decimal("440")  # both entries plus the approved expense of the period


async def test_partner_gate_confirmed_period_bills_unbilled_lines(tt_db):
    await _gate_project(tt_db, confirmed=True)
    period = dict(partner_billing_period_from=date(2026, 11, 1), partner_billing_period_to=date(2026, 11, 30))
    inv = await make_invoice(tt_db, lines=None, time_entry_ids=["g0"], **period)
    assert inv.subtotal == Decimal("240")  # ticked entry plus the period expense (expenses not restricted)
    rest = await make_invoice(tt_db, lines=None, **period)
    assert rest.subtotal == Decimal("200")
    with pytest.raises(HTTPException, match="Нет невыставленных"):
        await make_invoice(tt_db, lines=None, **period)


async def test_manual_number_and_billed_amount_validation(tt_db):
    await make_invoice(tt_db, invoice_number="KL-1")
    with pytest.raises(HTTPException) as err:
        await make_invoice(tt_db, invoice_number="KL-1")
    assert err.value.status_code == 409
    with pytest.raises(HTTPException, match="длинный"):
        await make_invoice(tt_db, invoice_number="X" * 65)
    with pytest.raises(HTTPException, match="greater than 0"):
        await make_invoice(tt_db, lines=None, billed_amount=Decimal("0"))


async def test_hour_package_fee_and_overage(tt_db):
    proj = await tt_db.get(TimeManagerClientProjectModel, "p1")
    proj.project_type = "hour_package"
    proj.package_hours_per_month = Decimal("3")
    proj.package_fee_amount = Decimal("1000")
    tt_db.add(UserHourlyRateModel(id="r1", auth_user_id=7, rate_kind="billable", amount=Decimal("100"), currency="USD", created_at=NOW))
    for i, hours in enumerate(("4", "4")):
        tt_db.add(TimeEntryModel(
            id=f"h{i}", auth_user_id=7, work_date=date(2026, 11, 3 + i), duration_seconds=14400, hours=Decimal(hours),
            rounded_hours=Decimal(hours), project_id="p1", is_billable=True, created_at=NOW,
        ))
    await tt_db.commit()
    inv = await make_invoice(tt_db, lines=None, time_entry_ids=["h0", "h1"])
    lines = await svc._invoice_lines_for_totals(tt_db, inv)
    kinds = sorted(line.line_kind for line in lines)
    assert kinds == ["package_fee", "time"]
    # 3 h package + 3 h carried from the unused October package = 6 h; 8 h worked → 2 h overage at 100
    assert inv.subtotal == Decimal("1200")
    again = await svc.patch_invoice_draft(
        tt_db, inv, actor_auth_user_id=1, replace_lines=[{"lineKind": "time", "timeEntryId": "h0"}, {"lineKind": "time", "timeEntryId": "h1"}],
    )
    assert again.subtotal == inv.subtotal


async def test_flat_fee_task_line(tt_db):
    from infrastructure.models import TimeManagerClientTaskModel

    tt_db.add(TimeManagerClientTaskModel(
        id="t1", project_id="p1", name="Registration", billing_mode="flat_fee", flat_fee_amount=Decimal("500"),
        flat_fee_currency="USD", created_at=NOW,
    ))
    tt_db.add(TimeEntryModel(
        id="f1", auth_user_id=7, work_date=date(2026, 11, 3), duration_seconds=3600, hours=Decimal("1"),
        rounded_hours=Decimal("1"), project_id="p1", task_id="t1", is_billable=True, description="", created_at=NOW,
    ))
    await tt_db.commit()
    inv = await make_invoice(tt_db, lines=None, time_entry_ids=["f1"])
    lines = await svc._invoice_lines_for_totals(tt_db, inv)
    assert lines[0].description.startswith("Registration:")
    assert inv.subtotal == Decimal("500")
