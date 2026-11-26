"""Invoice service branches: registry statuses, custom billed sums, fixed fee, numbering, payment documents."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import application.invoice_service as svc
from application.invoice_fx import FxRateBook
from support.tt_invoice import TT_NOW as NOW, make_invoice


def _inv(**kw):
    base = dict(status="sent", total_amount=Decimal("100"), amount_paid=Decimal("0"), due_date=date(2030, 1, 1))
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(("kw", "expected"), [
    ({"status": "canceled"}, "canceled"),
    ({"status": "client_paid"}, "paid"),
    ({"status": "issued"}, "issued"),
    ({"amount_paid": Decimal("100")}, "paid"),
    ({"status": "paid", "total_amount": Decimal("0")}, "paid"),
    ({"amount_paid": Decimal("40")}, "partial_paid"),
    ({"due_date": date(2020, 1, 1)}, "overdue"),
    ({"status": "draft", "due_date": date(2020, 1, 1)}, "draft"),
])
def test_effective_status(kw, expected):
    assert svc.effective_invoice_status(_inv(**kw)) == expected


def test_effective_status_with_paid_override():
    assert svc.effective_invoice_status(_inv(), amount_paid_override=Decimal("100")) == "paid"


def test_registry_status_codes():
    assert svc._registry_status_code("Оплачен.") == "paid"
    assert svc._registry_status_code("  отменен ") == "canceled"
    assert svc._registry_status_code("ОТМЕНЁН") == "canceled"
    assert svc._registry_status_code("что-то") is None


def test_billed_amount_overrides_pack():
    doc = svc.build_billed_amount_document_overrides(
        billed_amount=Decimal("1000"), currency="USD",
        service_lines=[("  ", Decimal("1")), ("Advice", Decimal("600")), ("Drafting", Decimal("400"))],
        fx={"fxRate": "12000", "fxBaseCurrency": "USD", "fxQuoteCurrency": "", "fxRateDate": None},
        expenses_amount=Decimal("50"),
    )
    assert doc["includedPageKeys"] == ["invoice"]
    legal = doc["legal"]
    assert legal["serviceDescriptionLine"] == "Advice" and len(legal["serviceLines"]) == 2
    assert legal["fxRate"] == "12000" and "fxQuoteCurrency" not in legal and "extraExpensesAmount" in legal
    plain = svc.build_billed_amount_document_overrides(billed_amount=Decimal("5"), currency="USD", service_description="Fee")
    assert plain["legal"]["serviceLines"][0]["description"] == "Fee"
    assert "legal" not in svc.build_billed_amount_document_overrides(billed_amount=Decimal("5"), currency="USD")


def _book():
    book = FxRateBook()
    book.add("USD", "UZS", date(2026, 10, 31), Decimal("12500"))
    book.add("USD", "EUR", date(2026, 10, 31), Decimal("0.9"))
    return book


def test_billed_fx_display():
    usd = svc.resolve_billed_amount_fx_display(_book(), billed_amount=Decimal("100"), currency="USD", issue_date=date(2026, 11, 26))
    assert usd["fxQuoteCurrency"] == "UZS" and usd["fxRate"] == "12500" and usd["fxRateDate"] == "2026-10-31"
    eur = svc.resolve_billed_amount_fx_display(_book(), billed_amount=Decimal("90"), currency="EUR", issue_date=date(2026, 11, 26))
    assert eur["fxQuoteCurrency"] == "EUR"
    assert svc.resolve_billed_amount_fx_display(_book(), billed_amount=Decimal("1"), currency="USD", issue_date=None) == {}
    assert svc.resolve_billed_amount_fx_display(FxRateBook(), billed_amount=Decimal("1"), currency="GBP", issue_date=date(2026, 1, 5)) == {}
    assert svc.last_day_of_previous_month(date(2026, 1, 10)) == date(2025, 12, 31)


def test_restrict_preview_keeps_only_ticked_lines():
    line = lambda tid=None, eid=None, total="10": SimpleNamespace(time_entry_id=tid, expense_request_id=eid, line_total=Decimal(total))
    preview = SimpleNamespace(
        lines=[line("t1"), line("t2"), line(eid="x1", total="5"), line(eid="x2", total="7"), line(total="3")],
        time_entry_ids=["t1", "t2"], expense_ids=["x1", "x2"],
        time_subtotal=0, expense_subtotal=0, expected_subtotal=0,
    )
    svc.restrict_partner_preview_to_selected_ids(preview, time_entry_ids=["t1"], expense_ids=["x2"])
    assert preview.time_entry_ids == ["t1"] and preview.expense_ids == ["x2"]
    assert preview.expected_subtotal == Decimal("17") and len(preview.lines) == 3
    untouched = SimpleNamespace(lines=[1])
    svc.restrict_partner_preview_to_selected_ids(untouched, time_entry_ids=None, expense_ids=None)
    assert untouched.lines == [1]


def test_overrides_json_helpers():
    assert svc._parse_document_overrides_json(json.dumps({"v": 1})) == {"v": 1}
    assert svc._parse_document_overrides_json("[1]") is None
    raw = svc._serialize_document_overrides({"cover": {"totalFormatted": "x"}})
    assert json.loads(raw)["cover"]["totalFormatted"] == "x"


async def test_custom_billed_amount_invoice(tt_db):
    inv = await make_invoice(
        tt_db, lines=None, billed_amount=Decimal("1200"), currency="USD",
        billed_service_lines=[("Advice", Decimal("700")), ("Court", Decimal("500"))],
    )
    assert inv.total_amount == Decimal("1200")
    doc = json.loads(inv.document_overrides_json)
    assert doc["legal"]["serviceLines"][1]["description"] == "Court"
    single = await make_invoice(tt_db, lines=None, billed_amount=Decimal("300"), currency="USD", service_description="")
    assert single.total_amount == Decimal("300")


async def test_fixed_fee_added_changed_and_removed(tt_db):
    inv = await make_invoice(tt_db)
    inv = await svc.patch_invoice_draft(
        tt_db, inv, actor_auth_user_id=1,
        extra_fixed_fee_specified=True, extra_fixed_fee_amount=Decimal("50"), extra_fixed_fee_description="",
    )
    await tt_db.commit()
    assert inv.subtotal == Decimal("350")
    doc = json.loads(inv.document_overrides_json)
    assert "350" in doc["cover"]["totalFormatted"]
    inv = await svc.patch_invoice_draft(
        tt_db, inv, actor_auth_user_id=1,
        extra_fixed_fee_specified=True, extra_fixed_fee_amount=Decimal("80"), extra_fixed_fee_description="Filing",
    )
    await tt_db.commit()
    assert inv.subtotal == Decimal("380")
    inv = await svc.patch_invoice_draft(
        tt_db, inv, actor_auth_user_id=1, extra_fixed_fee_specified=True, extra_fixed_fee_amount=Decimal("80"),
        extra_fixed_fee_description="Filing",
    )
    inv = await svc.patch_invoice_draft(tt_db, inv, actor_auth_user_id=1, extra_fixed_fee_specified=True, extra_fixed_fee_amount=None)
    await tt_db.commit()
    assert inv.subtotal == Decimal("300")


async def test_project_fixed_fee_applied_on_create(tt_db):
    proj = await tt_db.get(svc.TimeManagerClientProjectModel, "p1")
    proj.extra_fixed_fee_amount = Decimal("25")
    proj.extra_fixed_fee_description = "Admin fee"
    await tt_db.commit()
    inv = await make_invoice(tt_db)
    assert inv.subtotal == Decimal("325")


async def test_patch_number_dates_project_and_overrides(tt_db):
    first = await make_invoice(tt_db)
    second = await make_invoice(tt_db)
    with pytest.raises(HTTPException) as err:
        await svc.patch_invoice_draft(tt_db, second, actor_auth_user_id=1, invoice_number=first.invoice_number)
    assert err.value.status_code == 409
    with pytest.raises(HTTPException):
        await svc.patch_invoice_draft(tt_db, second, actor_auth_user_id=1, invoice_number="  ")
    with pytest.raises(HTTPException):
        await svc.patch_invoice_draft(tt_db, second, actor_auth_user_id=1, project_id="missing")
    inv = await svc.patch_invoice_draft(
        tt_db, second, actor_auth_user_id=1, invoice_number="KL-77", issue_date=date(2026, 11, 27),
        due_date=date(2026, 12, 27), tax_percent=Decimal("5"), tax2_percent=Decimal("1"), discount_percent=Decimal("2"),
        internal_note=" note ", internal_note_provided=True, project_id="",
        document_overrides={"v": 1, "cover": {}}, document_overrides_provided=True,
    )
    await tt_db.commit()
    assert inv.invoice_number == "KL-77" and inv.project_id is None and inv.internal_note == "note"
    with pytest.raises(HTTPException):
        await svc.patch_invoice_draft(tt_db, inv, actor_auth_user_id=1, replace_lines=[{"lineKind": "time"}])
    with pytest.raises(HTTPException):
        await svc.patch_invoice_draft(tt_db, inv, actor_auth_user_id=1, replace_lines=[{"lineKind": "expense", "expenseRequestId": "x"}])
    inv = await svc.patch_invoice_draft(
        tt_db, inv, actor_auth_user_id=1,
        replace_lines=[{"lineKind": "package_fee", "description": "Package", "quantity": "1", "unitAmount": "900"}],
    )
    assert inv.subtotal == Decimal("900")


async def test_registry_status_transitions(tt_db):
    inv = await make_invoice(tt_db)
    with pytest.raises(HTTPException):
        await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="???")
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Выставлен")
    assert inv.status == "issued"
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Выставлен")
    assert inv.status == "issued"
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Черновик")
    assert inv.status == "draft"
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Отправлен")
    assert inv.status == "sent"
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Просмотрен")
    assert inv.status == "viewed"
    with pytest.raises(HTTPException):
        await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Частично оплачен")
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Просрочен")
    assert inv.due_date < date.today()
    await svc.register_payment(tt_db, inv, actor_auth_user_id=1, amount=Decimal("100"), paid_at=NOW, payment_method=None, note=None)
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Частично оплачен")
    assert inv.status == "partial_paid" and inv.due_date >= date.today()
    with pytest.raises(HTTPException):
        await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Отправлен")
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Оплачен")
    assert inv.status == "paid"
    with pytest.raises(HTTPException):
        await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Просрочен")
    with pytest.raises(HTTPException):
        await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Частично оплачен")
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Аннулирован")
    assert inv.status == "canceled"
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Оплачен")
    assert inv.status == "paid"
    await tt_db.commit()


async def test_registry_note_status_on_canceled_and_draft_paths(tt_db):
    inv = await make_invoice(tt_db)
    inv = await svc.cancel_invoice(tt_db, inv, actor_auth_user_id=1)
    assert (await svc.cancel_invoice(tt_db, inv, actor_auth_user_id=1)).status == "canceled"
    inv = await svc.apply_registry_status(tt_db, inv, actor_auth_user_id=1, label="Ольге направить")
    assert inv.status == "send_to_olga"
    with pytest.raises(HTTPException):
        await svc.restore_invoice(tt_db, inv, actor_auth_user_id=1)
    other = await make_invoice(tt_db)
    other = await svc.apply_registry_status(tt_db, other, actor_auth_user_id=1, label="Оплачен")
    assert other.status == "paid"


async def test_payment_document_and_overpay(tt_db):
    inv = await make_invoice(tt_db)
    with pytest.raises(HTTPException):
        await svc.record_payment_confirmation_document(tt_db, inv, actor_auth_user_id=1, document_url="x")
    inv = await svc.send_invoice(tt_db, inv, actor_auth_user_id=1)
    with pytest.raises(HTTPException):
        await svc.record_payment_confirmation_document(tt_db, inv, actor_auth_user_id=1, document_url="x")
    with pytest.raises(HTTPException):
        await svc.register_payment(tt_db, inv, actor_auth_user_id=1, amount=Decimal("-1"), paid_at=None, payment_method=None, note=None)
    inv = await svc.register_payment(tt_db, inv, actor_auth_user_id=1, amount=None, paid_at=None, payment_method=None, note=None)
    with pytest.raises(HTTPException):
        await svc.register_payment(tt_db, inv, actor_auth_user_id=1, amount=None, paid_at=None, payment_method=None, note=None)
    with pytest.raises(HTTPException):
        await svc.record_payment_confirmation_document(tt_db, inv, actor_auth_user_id=1, document_url="  ")
    inv = await svc.record_payment_confirmation_document(tt_db, inv, actor_auth_user_id=1, document_url=" https://x/doc.pdf ")
    assert inv.payment_confirmation_document_url == "https://x/doc.pdf"
    with pytest.raises(HTTPException):
        await svc.unsend_invoice(tt_db, inv, actor_auth_user_id=1)
    await tt_db.commit()


async def test_stats_and_dict_details(tt_db):
    inv = await make_invoice(tt_db, tax_percent=Decimal("10"))
    await svc.send_invoice(tt_db, inv, actor_auth_user_id=1)
    inv.due_date = date.today() - timedelta(days=3)
    await tt_db.commit()
    stats = await svc.get_invoices_aggregated_stats(tt_db, project_id="p1", date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    assert stats
    data = await svc.invoice_to_dict_async(tt_db, inv)
    assert data["status"] in ("overdue", "sent")
