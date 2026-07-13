"""Task-level billing modes (hourly vs flat fee per time entry)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

BILLING_MODE_HOURLY = "hourly"
BILLING_MODE_FLAT_FEE = "flat_fee"

MEHNAT_TASK_NAME = "My mehnat registration"
MEHNAT_FLAT_FEE_AMOUNT = Decimal("230000")
MEHNAT_FLAT_FEE_CURRENCY = "UZS"


def normalize_billing_mode(raw: Any) -> str:
    s = str(raw or BILLING_MODE_HOURLY).strip().lower()
    if s in {BILLING_MODE_FLAT_FEE, "flat", "fixed", "fixed_fee"}:
        return BILLING_MODE_FLAT_FEE
    return BILLING_MODE_HOURLY


def is_flat_fee_task(task: Any | None) -> bool:
    if task is None:
        return False
    mode = normalize_billing_mode(getattr(task, "billing_mode", None))
    if mode != BILLING_MODE_FLAT_FEE:
        return False
    amt = getattr(task, "flat_fee_amount", None)
    if amt is None:
        return False
    try:
        return Decimal(str(amt)) > 0
    except Exception:
        return False


def flat_fee_for_task(task: Any | None) -> tuple[Decimal, str] | None:
    """Return (amount, currency) for a flat-fee task, else None."""
    if not is_flat_fee_task(task):
        return None
    amt = Decimal(str(getattr(task, "flat_fee_amount")))
    cur = (getattr(task, "flat_fee_currency", None) or "UZS").strip()[:10] or "UZS"
    return amt, cur


def mehnat_seed_billing() -> tuple[str, Decimal, str]:
    return BILLING_MODE_FLAT_FEE, MEHNAT_FLAT_FEE_AMOUNT, MEHNAT_FLAT_FEE_CURRENCY
