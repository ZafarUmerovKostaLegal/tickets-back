"""Invoice service against a real (SQLite) database: create → edit → send → pay → cancel → restore → delete."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

import application.invoice_service as svc
from infrastructure.models import TimeEntryModel, TimeManagerClientModel, UserHourlyRateModel
from support.tt_invoice import TT_NOW as NOW, make_invoice as _create


async def test_create_manual_invoice_totals_and_dict(tt_db):
    session = tt_db
    inv = await _create(session, tax_percent=Decimal("12"), discount_percent=Decimal("10"))
    assert inv.status == "draft" and inv.currency == "USD"
    assert inv.subtotal == Decimal("300")
    assert inv.total_amount > 0
    data = await svc.invoice_to_dict_async(session, inv)
    assert data["id"] == inv.id and data["currency"] == "USD"


async def test_unknown_client_and_foreign_project_are_rejected(tt_db):
    session = tt_db
    with pytest.raises(HTTPException) as err:
        await _create(session, client_id="nope")
    assert err.value.status_code == 404
    session.add(TimeManagerClientModel(id="c2", name="Other", created_at=NOW))
    await session.commit()
    with pytest.raises(HTTPException) as err:
        await _create(session, client_id="c2")
    assert err.value.status_code == 400


async def test_draft_edit_send_pay_cancel_restore_delete(tt_db):
    session = tt_db
    inv = await _create(tt_db)
    inv = await svc.patch_invoice_draft(
        session, inv, actor_auth_user_id=1, client_note="Thanks", client_note_provided=True,
        replace_lines=[{"lineKind": "manual", "description": "Advice", "quantity": "1", "unitAmount": "500"}],
    )
    await session.commit()
    assert inv.subtotal == Decimal("500") and inv.client_note == "Thanks"

    with pytest.raises(HTTPException):
        await svc.register_payment(session, inv, actor_auth_user_id=1, amount=None, paid_at=None, payment_method=None, note=None)

    inv = await svc.send_invoice(session, inv, actor_auth_user_id=1)
    await session.commit()
    assert inv.status == "sent"
    with pytest.raises(HTTPException):
        await svc.patch_invoice_draft(session, inv, actor_auth_user_id=1, invoice_number="X-1")

    inv = await svc.register_payment(
        session, inv, actor_auth_user_id=1, amount=Decimal("200"), paid_at=NOW, payment_method="bank", note=None,
    )
    await session.commit()
    assert inv.status == "partial_paid" and inv.amount_paid == Decimal("200")
    inv = await svc.register_payment(session, inv, actor_auth_user_id=1, amount=None, paid_at=NOW, payment_method=None, note=None)
    await session.commit()
    assert inv.status == "paid" and inv.amount_paid == inv.total_amount

    with pytest.raises(HTTPException):
        await svc.delete_draft_invoice(session, inv, actor_auth_user_id=1)

    inv = await svc.cancel_invoice(session, inv, actor_auth_user_id=1)
    await session.commit()
    assert inv.status == "canceled"
    with pytest.raises(HTTPException):
        await svc.register_payment(session, inv, actor_auth_user_id=1, amount=None, paid_at=None, payment_method=None, note=None)
    inv = await svc.restore_invoice(session, inv, actor_auth_user_id=1)
    await session.commit()
    assert inv.status == "paid"

    inv = await svc.cancel_invoice(session, inv, actor_auth_user_id=1)
    await svc.delete_draft_invoice(session, inv, actor_auth_user_id=1)
    await session.commit()
    assert await session.get(type(inv), inv.id) is None


async def test_unsend_and_viewed(tt_db):
    session = tt_db
    inv = await _create(tt_db)
    inv = await svc.send_invoice(session, inv, actor_auth_user_id=1)
    inv = await svc.mark_viewed(session, inv, actor_auth_user_id=1)
    await session.commit()
    inv = await svc.unsend_invoice(session, inv, actor_auth_user_id=1)
    await session.commit()
    assert inv.status == "draft"


async def test_time_entries_are_billed_once(tt_db):
    session = tt_db
    session.add(UserHourlyRateModel(id="r1", auth_user_id=7, rate_kind="billable", amount=Decimal("100"), currency="USD", created_at=NOW))
    for i, hours in enumerate(("1.5", "2")):
        session.add(TimeEntryModel(
            id=f"e{i}", auth_user_id=7, work_date=date(2026, 11, 10 + i), duration_seconds=int(Decimal(hours) * 3600),
            hours=Decimal(hours), rounded_hours=Decimal(hours), project_id="p1", is_billable=True,
            created_at=NOW,
        ))
    await session.commit()
    unbilled = await svc.list_unbilled_time_entries(session, project_id="p1", date_from=date(2026, 11, 1), date_to=date(2026, 11, 30))
    assert {row["id"] for row in unbilled} == {"e0", "e1"}
    assert all(row["flatFee"] is False for row in unbilled)

    inv = await _create(session, lines=None, time_entry_ids=["e0", "e1"])
    assert inv.subtotal == Decimal("350")
    unbilled = await svc.list_unbilled_time_entries(session, project_id="p1", date_from=date(2026, 11, 1), date_to=date(2026, 11, 30))
    assert unbilled == []
    with pytest.raises(HTTPException):
        await _create(session, lines=None, time_entry_ids=["e0"])

    stats = await svc.get_invoices_aggregated_stats(session, client_id="c1")
    assert stats


def test_pure_helpers():
    assert svc._money4("1.23456") == Decimal("1.2346")
    assert svc.last_day_of_previous_month(date(2026, 3, 15)) == date(2026, 2, 28)
    assert "USD" in svc.format_invoice_total_display(Decimal("1234.5"), "USD") or "$" in svc.format_invoice_total_display(Decimal("1234.5"), "USD")
    assert svc._registry_status_code("") is None
    assert svc._parse_document_overrides_json(None) is None
    assert svc._parse_document_overrides_json("not json") is None
    assert svc._serialize_document_overrides(None) is None
