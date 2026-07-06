from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from application.budget_mode import (
    budget_limit_hours,
    budget_limit_money,
    budget_mode,
    effective_budget_amount,
    normalize_budget_type_for_persist,
)


def _project(**kwargs):
    defaults = {
        "budget_hours": None,
        "budget_amount": None,
        "project_type": "time_and_materials",
        "progress_budget_amount": None,
        "fixed_fee_amount": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.unit
def test_budget_mode_hours_only():
    p = _project(budget_hours=Decimal("10"))
    assert budget_mode(p) == "hours"


@pytest.mark.unit
def test_budget_mode_money_from_progress():
    p = _project(project_type="time_and_materials", progress_budget_amount=Decimal("5000"))
    assert budget_mode(p) == "money"
    assert effective_budget_amount(p) == Decimal("5000")


@pytest.mark.unit
def test_budget_mode_fixed_fee():
    p = _project(project_type="fixed_fee", fixed_fee_amount=Decimal("10000"))
    assert budget_mode(p) == "money"


@pytest.mark.unit
def test_normalize_budget_type_hours_and_money():
    assert normalize_budget_type_for_persist(Decimal("1"), Decimal("2")) == "hours_and_money"


@pytest.mark.unit
def test_budget_limits():
    p = _project(budget_hours=Decimal("5"), progress_budget_amount=Decimal("100"))
    assert budget_limit_hours(p) == Decimal("5")
    assert budget_limit_money(p) == Decimal("100")
