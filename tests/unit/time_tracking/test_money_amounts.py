from decimal import Decimal

from application.money_amounts import money_product_hours_rate


def test_money_product_rounds_half_up():
    assert money_product_hours_rate(Decimal("1.005"), Decimal("100")) == Decimal("100.50")
    assert money_product_hours_rate(Decimal("2"), Decimal("33.333")) == Decimal("66.67")
