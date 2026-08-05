"""Unit tests for manual billed-amount override on invoice create."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from support.service_path import ensure_service_in_path


def test_format_invoice_total_display_matches_fe_style():
    ensure_service_in_path("time_tracking")
    from application.invoice_service import format_invoice_total_display

    assert format_invoice_total_display(Decimal("360"), "USD") == "USD 360.00"
    assert format_invoice_total_display(Decimal("1234.5"), "eur") == "EUR 1,234.50"
    assert format_invoice_total_display(Decimal("-10.1"), "UZS") == "−UZS 10.10"


def test_build_billed_amount_document_overrides_legal_page_only():
    ensure_service_in_path("time_tracking")
    from application.invoice_service import build_billed_amount_document_overrides

    ovr = build_billed_amount_document_overrides(
        billed_amount=Decimal("360.00"),
        currency="USD",
        service_description="Legal services rendered in July 2026",
    )
    assert ovr["v"] == 1
    assert ovr["includedPageKeys"] == ["invoice"]
    assert ovr["cover"]["totalFormatted"] == "USD 360.00"
    assert ovr["legal"]["serviceDescriptionLine"] == "Legal services rendered in July 2026"


def test_build_billed_amount_document_overrides_without_description():
    ensure_service_in_path("time_tracking")
    from application.invoice_service import build_billed_amount_document_overrides

    ovr = build_billed_amount_document_overrides(
        billed_amount=Decimal("100"),
        currency="EUR",
        service_description=None,
    )
    assert ovr["includedPageKeys"] == ["invoice"]
    assert "legal" not in ovr
    assert ovr["cover"]["totalFormatted"] == "EUR 100.00"


def test_zero_linkage_lines_and_manual_billed_math():
    """Simulate post-override totals: linkage at 0 + manual billed = invoice total."""
    ensure_service_in_path("time_tracking")
    from application.invoice_service import _money4

    linkage = [
        SimpleNamespace(line_kind="time", line_total=Decimal("100.50"), time_entry_id="t1"),
        SimpleNamespace(line_kind="expense", line_total=Decimal("50.00"), expense_request_id="e1"),
    ]
    for ln in linkage:
        ln.unit_amount = Decimal(0)
        ln.line_total = Decimal(0)

    billed = _money4(Decimal("360"))
    manual = SimpleNamespace(line_kind="manual", line_total=billed)
    all_lines = [*linkage, manual]
    subtotal = _money4(sum(_money4(x.line_total) for x in all_lines))
    assert subtotal == Decimal("360.0000")
    assert getattr(linkage[0], "time_entry_id", None) == "t1"
    assert getattr(linkage[1], "expense_request_id", None) == "e1"
    assert manual.line_kind == "manual"


def test_invoice_create_body_accepts_billed_amount():
    ensure_service_in_path("time_tracking")
    from presentation.schemas_invoices import InvoiceCreateBody
    from datetime import date

    body = InvoiceCreateBody.model_validate(
        {
            "clientId": "c1",
            "projectId": "p1",
            "issueDate": "2026-08-05",
            "dueDate": "2026-09-20",
            "timeEntryIds": ["te-1"],
            "billedAmount": "360.00",
            "serviceDescription": "Legal services rendered in July 2026",
            "partnerBillingPeriodFrom": "2026-07-01",
            "partnerBillingPeriodTo": "2026-07-31",
        }
    )
    assert body.billed_amount == Decimal("360.00")
    assert body.service_description == "Legal services rendered in July 2026"
    assert body.time_entry_ids == ["te-1"]
    assert body.partner_billing_period_from == date(2026, 7, 1)


def test_invoice_create_body_accepts_billed_amount_without_lines():
    ensure_service_in_path("time_tracking")
    from presentation.schemas_invoices import InvoiceCreateBody

    body = InvoiceCreateBody.model_validate(
        {
            "clientId": "c1",
            "projectId": "p1",
            "issueDate": "2026-08-05",
            "dueDate": "2026-09-20",
            "billedAmount": "3000",
            "serviceDescription": "Legal services rendered in August 2024",
        }
    )
    assert body.billed_amount == Decimal("3000")
    assert body.time_entry_ids is None
    assert body.expense_ids is None
    assert body.partner_billing_period_from is None


def test_invoice_create_body_rejects_non_positive_billed_amount():
    ensure_service_in_path("time_tracking")
    from presentation.schemas_invoices import InvoiceCreateBody
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InvoiceCreateBody.model_validate(
            {
                "clientId": "c1",
                "issueDate": "2026-08-05",
                "dueDate": "2026-09-20",
                "billedAmount": "0",
            }
        )
