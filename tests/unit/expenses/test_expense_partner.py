

from datetime import date
from decimal import Decimal

import pytest

from application.expense_service import (
    is_partner_expense,
    normalize_payment_method,
    normalize_reimbursement_card_number,
    validate_payment_details,
    validate_submit_fields,
)


def test_is_partner_expense():
    assert is_partner_expense("partner_expense") is True
    assert is_partner_expense(" food ") is False


def test_is_partner_org_role():
    from application.expense_service import is_partner_org_role

    assert is_partner_org_role("Партнер") is True
    assert is_partner_org_role("Партнёр") is True
    assert is_partner_org_role("Сотрудник") is False


@pytest.mark.parametrize("value", ["cash", "transfer", "card"])
def test_allowed_payment_methods(value: str):
    assert normalize_payment_method(value) == value


@pytest.mark.parametrize("value", ["other_payment", "other", "bank_card"])
def test_removed_payment_methods_are_rejected(value: str):
    with pytest.raises(ValueError, match="paymentMethod"):
        normalize_payment_method(value)


def test_payment_method_is_required():
    with pytest.raises(ValueError, match="paymentMethod is required"):
        validate_payment_details(None, None)


def test_cash_payment_requires_and_normalizes_reimbursement_card():
    assert validate_payment_details("cash", "8600 1234-1234 5678") == (
        "cash",
        "8600123412345678",
    )
    assert normalize_reimbursement_card_number("8600 1234 1234 5678") == "8600123412345678"
    with pytest.raises(ValueError, match="required"):
        validate_payment_details("cash", None)
    with pytest.raises(ValueError, match="exactly 16 digits"):
        validate_payment_details("cash", "8600 1234")


def test_non_cash_payment_drops_reimbursement_card():
    assert validate_payment_details("transfer", "8600123412345678") == ("transfer", None)


def test_partner_submit_relaxes_reimbursable_requirements():

    validate_submit_fields(
        description="Партнёр",
        expense_date=date(2026, 1, 10),
        amount_uzs=Decimal("100.00"),
        exchange_rate=Decimal("12500"),
        expense_type="partner_expense",
        expense_subtype="partner_fuel",
        is_reimbursable=True,
        payment_method="transfer",
        reimbursement_card_number=None,
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
        amount_uzs=Decimal("100.00"),
        exchange_rate=Decimal("12500"),
        expense_type="transport",
        expense_subtype=None,
        is_reimbursable=True,
        payment_method="cash",
        reimbursement_card_number="8600123412345678",
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
            amount_uzs=Decimal("999999"),
            exchange_rate=Decimal("12500"),
            expense_type="partner_expense",
            expense_subtype="partner_fuel",
            is_reimbursable=False,
            payment_method="card",
            reimbursement_card_number=None,
            comment=None,
            project_id=None,
            attachment_count=0,
            expense_amount_limit_uzs=Decimal("100"),
            payment_document_count=0,
            payment_receipt_count=0,
        )
