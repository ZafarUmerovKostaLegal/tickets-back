from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from application.access_control import (
    _can_manage_tt,
    _can_view_tt,
    ensure_can_view_tt_reports,
)


@pytest.mark.unit
def test_can_view_tt_for_admin():
    assert _can_view_tt({"role": "Администратор"}) is True


@pytest.mark.unit
def test_can_manage_tt_for_partner():
    assert _can_manage_tt({"role": "Партнер"}) is True


@pytest.mark.unit
def test_employee_cannot_manage_tt():
    assert _can_manage_tt({"role": "Сотрудник"}) is False


@pytest.mark.unit
def test_full_ops_no_reports_cannot_view_reports():
    with pytest.raises(HTTPException) as exc:
        ensure_can_view_tt_reports({"role": "Сотрудник", "position": "Accountant"})
    assert exc.value.status_code == 403
