"""Unit helpers for pay / unpay / unapprove authorization rules."""
from presentation.schemas import ExpenseCreateBody


def test_transfer_create_reimbursable_without_card_ok():
    body = ExpenseCreateBody.model_validate({
        "description": "Перевод поставщику",
        "expenseDate": "2026-07-30",
        "amountUzs": "150000",
        "exchangeRate": "12500",
        "expenseType": "services",
        "isReimbursable": True,
        "paymentMethod": "transfer",
    })
    assert body.payment_method == "transfer"
    assert body.reimbursement_card_number is None
    assert body.is_reimbursable is True
