from decimal import Decimal

from application.money_amounts import money_product_hours_rate, to_decimal


def test_money_product_rounds_half_up():
    assert money_product_hours_rate(Decimal("1.005"), Decimal("100")) == Decimal("100.50")
    assert money_product_hours_rate(Decimal("2"), Decimal("33.333")) == Decimal("66.67")


def test_to_decimal_coerces_int():
    assert to_decimal(395) == Decimal("395")
    assert to_decimal(0) == Decimal("0")
    assert to_decimal(None) == Decimal("0")
    assert money_product_hours_rate(2, 150) == Decimal("300.00")
