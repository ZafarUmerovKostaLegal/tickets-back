"""Statuses shown on the 2026 (system) invoice registry tab."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from support.service_path import ensure_service_in_path


def _invoice(**kwargs):
    base = {
        "status": "draft",
        "total_amount": Decimal("1000"),
        "amount_paid": Decimal("0"),
        "due_date": date.today() + timedelta(days=10),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_system_status_labels():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import _system_status

    assert _system_status(_invoice(status="draft")) == "Черновик"
    assert _system_status(_invoice(status="sent")) == "Отправлен"
    assert _system_status(_invoice(status="viewed")) == "Просмотрен"
    assert _system_status(_invoice(status="canceled", amount_paid=Decimal("1000"))) == "Отменён"


def test_system_status_paid_when_balance_is_gone():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import _system_status

    inv = _invoice(status="sent", total_amount=Decimal("500"), amount_paid=Decimal("500"))
    assert _system_status(inv) == "Оплачен"


def test_system_status_partial_and_overdue():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import _system_status

    partial = _invoice(status="sent", total_amount=Decimal("1000"), amount_paid=Decimal("100"))
    assert _system_status(partial) == "Частично оплачен"

    overdue = _invoice(status="sent", due_date=date.today() - timedelta(days=1))
    assert _system_status(overdue) == "Просрочен"

    partial_overdue = _invoice(
        status="sent",
        total_amount=Decimal("1000"),
        amount_paid=Decimal("100"),
        due_date=date.today() - timedelta(days=1),
    )
    assert _system_status(partial_overdue) == "Просрочен"


def test_manual_rows_sort_by_number_not_text():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import _seq_sort_key

    ordered = sorted(["10", "2", "1", "100", ""], key=_seq_sort_key)
    assert ordered == ["1", "2", "10", "100", ""]
