from decimal import Decimal

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


def test_cash_partner_role_and_position():
    assert is_cash_partner({"role": "Партнёр", "position": None})
    assert is_cash_partner({"role": "Юрист", "position": "Партнер"})
    assert not is_cash_partner({"role": "Администратор", "position": "Юрист"})
    assert not is_cash_partner({"role": "Сотрудник", "position": None})
