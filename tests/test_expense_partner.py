

from datetime import date
from decimal import Decimal

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("expenses")

from application.expense_service import is_partner_expense, validate_submit_fields


def test_is_partner_expense():
    assert is_partner_expense("partner_expense") is True
    assert is_partner_expense(" food ") is False


def test_partner_submit_relaxes_reimbursable_requirements():

    validate_submit_fields(
        description="Партнёр",
        expense_date=date(2026, 1, 10),
        payment_deadline=None,
        amount_uzs=Decimal("100.00"),
        exchange_rate=Decimal("12500"),
        expense_type="partner_expense",
        expense_subtype="partner_fuel",
        is_reimbursable=True,
        comment=None,
        project_id=None,
        attachment_count=0,
        expense_amount_limit_uzs=None,
        payment_document_count=0,
        payment_receipt_count=0,
    )


def test_reimbursable_without_project_allowed_when_docs_ok():

    validate_submit_fields(
        description="Такси",
        expense_date=date(2026, 1, 10),
        payment_deadline=None,
        amount_uzs=Decimal("100.00"),
        exchange_rate=Decimal("12500"),
        expense_type="transport",
        expense_subtype=None,
        is_reimbursable=True,
        comment=None,
        project_id=None,
        attachment_count=0,
        expense_amount_limit_uzs=None,
        payment_document_count=1,
        payment_receipt_count=0,
    )


def test_partner_submit_still_enforces_amount_limit():
    with pytest.raises(ValueError, match="exceeds"):
        validate_submit_fields(
            description="Партнёр",
            expense_date=date(2026, 1, 10),
            payment_deadline=None,
            amount_uzs=Decimal("999999"),
            exchange_rate=Decimal("12500"),
            expense_type="partner_expense",
            expense_subtype="partner_fuel",
            is_reimbursable=False,
            comment=None,
            project_id=None,
            attachment_count=0,
            expense_amount_limit_uzs=Decimal("100"),
            payment_document_count=0,
            payment_receipt_count=0,
        )
