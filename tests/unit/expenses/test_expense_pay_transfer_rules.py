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


def test_transfer_create_non_reimbursable_without_card_ok():
    """Client-non-reimbursable vendor transfer is a valid create; pay is allowed for moderators."""
    body = ExpenseCreateBody.model_validate({
        "description": "Госпошлина перечислением",
        "expenseDate": "2026-09-03",
        "amountUzs": "924000",
        "exchangeRate": "11813",
        "expenseType": "client_expense",
        "isReimbursable": False,
        "paymentMethod": "transfer",
    })
    assert body.payment_method == "transfer"
    assert body.is_reimbursable is False
