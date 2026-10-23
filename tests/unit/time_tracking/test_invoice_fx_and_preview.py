"""Unit tests for invoice FX conversion and partner invoice preview helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from support.service_path import ensure_service_in_path


def test_fx_same_currency_no_rate_needed():
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook

    book = FxRateBook()
    conv = book.convert(Decimal("100.50"), "UZS", "UZS", date(2026, 7, 1))
    assert conv.converted_amount == Decimal("100.5000")
    assert conv.fx_rate == Decimal(1)
    assert conv.source_currency == "UZS"
    assert conv.target_currency == "UZS"


def test_fx_direct_pair_uzs_to_eur():
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook

    book = FxRateBook()
    book.add("UZS", "EUR", date(2026, 7, 1), Decimal("0.00008"))
    conv = book.convert(Decimal("12500000"), "UZS", "EUR", date(2026, 7, 15))
    assert conv.fx_rate == Decimal("0.00008")
    assert conv.converted_amount == Decimal("1000.0000")


def test_fx_cross_via_usd():
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook

    book = FxRateBook()
    book.add("UZS", "USD", date(2026, 1, 1), Decimal("0.00008"))
    book.add("USD", "EUR", date(2026, 1, 1), Decimal("0.92"))
    conv = book.convert(Decimal("12500000"), "UZS", "EUR", date(2026, 6, 1))
    assert conv.converted_amount == Decimal("920.0000")


def test_fx_inverse_pair():
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook

    book = FxRateBook()
    book.add("EUR", "USD", date(2026, 1, 1), Decimal("1.1"))
    conv = book.convert(Decimal("110"), "USD", "EUR", date(2026, 3, 1))
    assert conv.converted_amount == Decimal("100.0000")


def test_fx_missing_rate_raises_400():
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook

    book = FxRateBook()
    with pytest.raises(HTTPException) as ei:
        book.convert(Decimal("10"), "UZS", "EUR", date(2026, 7, 1))
    assert ei.value.status_code == 400
    assert "FX" in str(ei.value.detail) or "курса" in str(ei.value.detail).lower()


def test_fx_uses_latest_rate_on_or_before_date():
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook

    book = FxRateBook()
    book.add("USD", "EUR", date(2026, 1, 1), Decimal("0.90"))
    book.add("USD", "EUR", date(2026, 6, 1), Decimal("0.92"))
    early = book.convert(Decimal("100"), "USD", "EUR", date(2026, 3, 1))
    late = book.convert(Decimal("100"), "USD", "EUR", date(2026, 7, 1))
    assert early.converted_amount == Decimal("90.0000")
    assert late.converted_amount == Decimal("92.0000")


def test_convert_expense_amount_uzs_invoice_uses_amount_uzs_as_is():
    """Regression: UZS expense must not be rebuilt via USD→UZS at a newer FX rate."""
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook, convert_expense_amount

    book = FxRateBook()
    book.add("USD", "UZS", date(2026, 8, 1), Decimal("12006.39"))
    row = {
        "amount_uzs": 12360000,
        "equivalent_amount": 1037.81,
        "expense_date": "2026-07-15",
    }
    conv = convert_expense_amount(book, row, "UZS", date(2026, 8, 5))
    assert conv.source_currency == "UZS"
    assert conv.target_currency == "UZS"
    assert conv.converted_amount == Decimal("12360000.0000")
    assert conv.fx_rate == Decimal(1)


def test_convert_expense_amount_usd_invoice_from_uzs():
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook, convert_expense_amount

    book = FxRateBook()
    book.add("USD", "UZS", date(2026, 7, 1), Decimal("11909.79"))
    row = {
        "amount_uzs": 12360000,
        "equivalent_amount": 1037.81,
        "expense_date": "2026-07-15",
    }
    conv = convert_expense_amount(book, row, "USD", date(2026, 8, 5))
    assert conv.source_currency == "UZS"
    assert conv.source_amount == Decimal("12360000.0000")
    assert conv.target_currency == "USD"
    assert conv.converted_amount == Decimal("1037.7456")


def test_convert_expense_amount_falls_back_to_usd_equivalent():
    ensure_service_in_path("time_tracking")
    from application.invoice_fx import FxRateBook, convert_expense_amount

    book = FxRateBook()
    book.add("USD", "UZS", date(2026, 8, 1), Decimal("12000"))
    row = {
        "amount_uzs": 0,
        "equivalent_amount": 100,
        "expense_date": "2026-08-01",
    }
    conv = convert_expense_amount(book, row, "UZS", date(2026, 8, 5))
    assert conv.source_currency == "USD"
    assert conv.converted_amount == Decimal("1200000.0000")


def test_partner_preview_dataclass_as_dict():
    ensure_service_in_path("time_tracking")
    from application.partner_confirmed_invoice_preview import (
        PartnerInvoicePreview,
        PartnerInvoicePreviewLine,
    )

    line = PartnerInvoicePreviewLine(
        line_kind="time",
        description="Work",
        quantity=Decimal("2"),
        unit_amount=Decimal("100"),
        line_total=Decimal("200"),
        source_currency="UZS",
        source_amount=Decimal("2500000"),
        fx_rate=Decimal("0.00008"),
        time_entry_id="te-1",
    )
    preview = PartnerInvoicePreview(
        currency="EUR",
        expected_subtotal=Decimal("200"),
        time_subtotal=Decimal("200"),
        expense_subtotal=Decimal("0"),
        package_fee_subtotal=Decimal("0"),
        time_entry_ids=["te-1"],
        lines=[line],
        project_currency="UZS",
    )
    d = preview.as_dict()
    assert d["currency"] == "EUR"
    assert d["expectedSubtotal"] == 200.0
    assert d["timeEntryIds"] == ["te-1"]
    assert d["lines"][0]["sourceCurrency"] == "UZS"
    assert d["lines"][0]["fxRate"] == 0.00008


def _preview_line(*, time_id=None, expense_id=None, amount="100"):
    from application.partner_confirmed_invoice_preview import PartnerInvoicePreviewLine

    return PartnerInvoicePreviewLine(
        line_kind="time" if time_id else "expense",
        description="line",
        quantity=Decimal(1),
        unit_amount=Decimal(amount),
        line_total=Decimal(amount),
        source_currency="USD",
        source_amount=Decimal(amount),
        fx_rate=Decimal(1),
        time_entry_id=time_id,
        expense_request_id=expense_id,
    )


def test_restrict_partner_preview_keeps_only_selected_time_entries():
    ensure_service_in_path("time_tracking")
    from application.invoice_service import restrict_partner_preview_to_selected_ids
    from application.partner_confirmed_invoice_preview import PartnerInvoicePreview

    preview = PartnerInvoicePreview(
        currency="USD",
        expected_subtotal=Decimal("300"),
        time_subtotal=Decimal("300"),
        expense_subtotal=Decimal("0"),
        package_fee_subtotal=Decimal("0"),
        time_entry_ids=["a", "b", "c"],
        expense_ids=["e1"],
        lines=[
            _preview_line(time_id="a", amount="100"),
            _preview_line(time_id="b", amount="100"),
            _preview_line(time_id="c", amount="100"),
            _preview_line(expense_id="e1", amount="50"),
        ],
        project_currency="USD",
    )
    restrict_partner_preview_to_selected_ids(
        preview,
        time_entry_ids=["b"],
        expense_ids=[],
    )
    assert preview.time_entry_ids == ["b"]
    assert preview.expense_ids == []
    assert [ln.time_entry_id or ln.expense_request_id for ln in preview.lines] == ["b"]
    assert preview.time_subtotal == Decimal("100.0000")
    assert preview.expense_subtotal == Decimal("0.0000")
