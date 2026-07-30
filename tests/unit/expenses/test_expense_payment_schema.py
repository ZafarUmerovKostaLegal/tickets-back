from pydantic import ValidationError
import pytest

from presentation.schemas import ExpenseCreateBody


def _body(**overrides):
    value = {
        "description": "Такси",
        "expenseDate": "2026-07-30",
        "amountUzs": "150000",
        "exchangeRate": "12500",
        "expenseType": "transport",
        "isReimbursable": True,
        "paymentMethod": "transfer",
    }
    value.update(overrides)
    return value


def test_create_requires_payment_method():
    body = _body()
    del body["paymentMethod"]
    with pytest.raises(ValidationError):
        ExpenseCreateBody.model_validate(body)


def test_cash_create_requires_reimbursement_card_number():
    with pytest.raises(ValidationError, match="reimbursementCardNumber is required"):
        ExpenseCreateBody.model_validate(_body(paymentMethod="cash"))


def test_cash_create_normalizes_reimbursement_card_number():
    body = ExpenseCreateBody.model_validate(_body(
        paymentMethod="cash",
        reimbursementCardNumber="8600 1234 1234 5678",
    ))
    assert body.payment_method == "cash"
    assert body.reimbursement_card_number == "8600123412345678"


def test_non_cash_create_does_not_keep_reimbursement_card_number():
    body = ExpenseCreateBody.model_validate(_body(
        paymentMethod="card",
        reimbursementCardNumber="8600123412345678",
    ))
    assert body.reimbursement_card_number is None
