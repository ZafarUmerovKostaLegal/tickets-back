from datetime import datetime, timezone
from decimal import Decimal

from application.cash_ledger import (
    PaidCashExpense,
    is_manual_cash_entry,
    manual_balance_delta,
    plan_reimbursement_actions,
    propagate_cash_delta,
)
from application.cash_money import cash_movement, format_money, parse_amount
from presentation.routes.cash import is_cash_partner


def test_parse_amount_spaces_and_comma():
    assert parse_amount("298 000") == Decimal("298000.00")
    assert parse_amount("1 500,50") == Decimal("1500.50")
    assert parse_amount("") is None
    assert parse_amount("нет") is None


def test_format_money_drops_zero_cents():
    assert format_money(Decimal("298000")) == "298000"
    assert format_money(Decimal("10.5")) == "10.50"


def test_cash_movement_text():
    text = cash_movement(Decimal("298000"), "Потрачено", Decimal("50000"), Decimal("248000"), "канцелярия")
    assert "Остаток в кассе: 298000" in text
    assert "Потрачено: 50000 (канцелярия)" in text
    assert "Остаток на текущий момент: 248000" in text


def _paid(expense_id: str, amount: str, when: datetime | None, description: str = "пошлина") -> PaidCashExpense:
    return PaidCashExpense(expense_id, Decimal(amount), description, when, 1)


def test_reimbursement_after_balance_is_subtracted_once():
    cutoff = datetime(2026, 9, 23, 7, 59, tzinfo=timezone.utc)
    old = _paid("KL1", "1000", datetime(2026, 9, 23, 7, 0, tzinfo=timezone.utc), "старая")
    fresh = _paid("KL2", "59900", datetime(2026, 9, 23, 8, 10, tzinfo=timezone.utc))
    done, actions = plan_reimbursement_actions(
        balance=Decimal("300000"),
        baseline_done=False,
        cutoff=cutoff,
        tracked={},
        paid=[old, fresh],
    )
    assert done is True
    assert [item.kind for item in actions] == ["track", "subtract"]
    assert actions[0].expense_id == "KL1"
    assert actions[1].expense_id == "KL2"
    assert actions[1].before == Decimal("300000")
    assert actions[1].after == Decimal("240100")
    assert actions[1].description == "пошлина"

    again_done, again = plan_reimbursement_actions(
        balance=actions[1].after,
        baseline_done=True,
        cutoff=cutoff,
        tracked={"KL1": Decimal("1000"), "KL2": Decimal("59900")},
        paid=[old, fresh],
    )
    assert again_done is True
    assert again == []


def test_cancelled_reimbursement_is_added_back():
    _, actions = plan_reimbursement_actions(
        balance=Decimal("240100"),
        baseline_done=True,
        cutoff=datetime(2026, 9, 23, 7, 59, tzinfo=timezone.utc),
        tracked={"KL2": Decimal("59900")},
        paid=[],
    )
    assert len(actions) == 1
    assert actions[0].kind == "restore"
    assert actions[0].after == Decimal("300000")


class _Point:
    def __init__(self, kind: str, before: str | None, after: str):
        self.kind = kind
        self.balance_before = None if before is None else Decimal(before)
        self.balance_after = Decimal(after)


def test_manual_entry_is_hand_typed_expense_or_topup():
    assert is_manual_cash_entry("expense", None)
    assert is_manual_cash_entry("topup", "")
    assert not is_manual_cash_entry("expense", "KL-1")
    assert not is_manual_cash_entry("set", None)


def test_edit_shifts_later_rows_and_live_balance():
    later = [_Point("topup", "100", "150"), _Point("expense", "150", "120")]
    delta = manual_balance_delta(kind="expense", old_amount=Decimal("40"), new_amount=Decimal("10"))
    assert delta == Decimal("30")
    assert propagate_cash_delta(later, delta) is True
    assert later[0].balance_before == Decimal("130")
    assert later[0].balance_after == Decimal("180")
    assert later[1].balance_after == Decimal("150")


def test_shift_stops_at_balance_set():
    later = [_Point("expense", "100", "80"), _Point("set", "80", "500"), _Point("topup", "500", "510")]
    assert propagate_cash_delta(later, Decimal("-20")) is False
    assert later[0].balance_after == Decimal("60")
    assert later[1].balance_before == Decimal("60")
    assert later[1].balance_after == Decimal("500")
    assert later[2].balance_before == Decimal("500")


def test_delete_manual_expense_returns_the_amount():
    assert manual_balance_delta(kind="expense", old_amount=Decimal("100000"), new_amount=None) == Decimal("100000")
    assert manual_balance_delta(kind="topup", old_amount=Decimal("10000"), new_amount=None) == Decimal("-10000")


def test_cash_partner_role_and_position():
    assert is_cash_partner({"role": "Партнёр", "position": None})
    assert is_cash_partner({"role": "Юрист", "position": "Партнер"})
    assert not is_cash_partner({"role": "Администратор", "position": "Юрист"})
    assert not is_cash_partner({"role": "Сотрудник", "position": None})
