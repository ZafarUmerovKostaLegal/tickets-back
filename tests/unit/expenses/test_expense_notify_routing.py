from decimal import Decimal
from types import SimpleNamespace

from infrastructure.expense_notify_routing import resolve_expense_notify_recipients


def _settings(**kwargs) -> SimpleNamespace:
    base = {
        "expense_notify_to": "high@kostalegal.com",
        "expense_notify_to_low": "low@kostalegal.com,low2@kostalegal.com",
        "expense_approval_low_limit_uzs": Decimal("3000000"),
        "expense_notify_routing_json": "",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_low_limit_routes_to_low_recipients():
    s = _settings()
    to = resolve_expense_notify_recipients(
        s,
        department_id=None,
        expense_type="purchase",
        project_id=None,
        is_reimbursable=True,
        amount_uzs=Decimal("2999999.99"),
    )
    assert to == ["low@kostalegal.com", "low2@kostalegal.com"]


def test_above_limit_routes_to_default_notify_to():
    s = _settings()
    to = resolve_expense_notify_recipients(
        s,
        department_id=None,
        expense_type="purchase",
        project_id=None,
        is_reimbursable=True,
        amount_uzs=Decimal("3000000.01"),
    )
    assert to == ["high@kostalegal.com"]


def test_exact_limit_uses_low_tier():
    s = _settings()
    to = resolve_expense_notify_recipients(
        s,
        department_id=None,
        expense_type="purchase",
        project_id=None,
        is_reimbursable=False,
        amount_uzs=Decimal("3000000"),
    )
    assert to == ["low@kostalegal.com", "low2@kostalegal.com"]


def test_without_low_list_falls_back_to_notify_to():
    s = _settings(expense_notify_to_low="")
    to = resolve_expense_notify_recipients(
        s,
        department_id=None,
        expense_type="purchase",
        project_id=None,
        is_reimbursable=True,
        amount_uzs=Decimal("1000"),
    )
    assert to == ["high@kostalegal.com"]


def test_routing_json_amount_max_overrides_simple_tier():
    s = _settings(
        expense_notify_routing_json="""
        {
          "rules": [
            { "if": { "amountUzsMax": 3000000 }, "to": ["json-low@kostalegal.com"] }
          ],
          "default": ["json-high@kostalegal.com"]
        }
        """,
    )
    low = resolve_expense_notify_recipients(
        s,
        department_id=None,
        expense_type="purchase",
        project_id=None,
        is_reimbursable=True,
        amount_uzs=Decimal("1000000"),
    )
    high = resolve_expense_notify_recipients(
        s,
        department_id=None,
        expense_type="purchase",
        project_id=None,
        is_reimbursable=True,
        amount_uzs=Decimal("5000000"),
    )
    assert low == ["json-low@kostalegal.com"]
    assert high == ["json-high@kostalegal.com"]


def test_routing_json_amount_min():
    s = _settings(
        expense_notify_to_low="",
        expense_notify_routing_json="""
        {
          "rules": [
            { "if": { "amountUzsMin": 3000000.01 }, "to": ["big@kostalegal.com"] }
          ],
          "default": ["small@kostalegal.com"]
        }
        """,
    )
    to = resolve_expense_notify_recipients(
        s,
        department_id=None,
        expense_type="purchase",
        project_id=None,
        is_reimbursable=True,
        amount_uzs=Decimal("4000000"),
    )
    assert to == ["big@kostalegal.com"]
