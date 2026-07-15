from decimal import Decimal
from types import SimpleNamespace

from application.task_billing import (
    BILLING_MODE_FLAT_FEE,
    BILLING_MODE_HOURLY,
    flat_fee_for_task,
    is_flat_fee_task,
    mehnat_seed_billing,
    normalize_billing_mode,
)


def test_normalize_billing_mode():
    assert normalize_billing_mode(None) == BILLING_MODE_HOURLY
    assert normalize_billing_mode("FLAT") == BILLING_MODE_FLAT_FEE
    assert normalize_billing_mode("fixed_fee") == BILLING_MODE_FLAT_FEE
    assert normalize_billing_mode("hourly") == BILLING_MODE_HOURLY


def test_is_flat_fee_task():
    assert not is_flat_fee_task(None)
    assert not is_flat_fee_task(SimpleNamespace(billing_mode="hourly", flat_fee_amount=10))
    assert is_flat_fee_task(
        SimpleNamespace(billing_mode="flat_fee", flat_fee_amount=Decimal("100"))
    )
    assert not is_flat_fee_task(
        SimpleNamespace(billing_mode="flat_fee", flat_fee_amount=0)
    )


def test_flat_fee_for_task_and_mehnat():
    task = SimpleNamespace(
        billing_mode="flat_fee",
        flat_fee_amount="230000",
        flat_fee_currency="uzs",
    )
    assert flat_fee_for_task(task) == (Decimal("230000"), "uzs")
    mode, amt, cur = mehnat_seed_billing()
    assert mode == BILLING_MODE_FLAT_FEE
    assert amt > 0
    assert cur == "UZS"
