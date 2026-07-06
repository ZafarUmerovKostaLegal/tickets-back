from __future__ import annotations

import pytest

from application.report_viewer_scope import viewer_sees_all_report_projects


@pytest.mark.unit
def test_admin_sees_all_projects():
    assert viewer_sees_all_report_projects({"role": "Администратор"}) is True


@pytest.mark.unit
def test_it_sees_all_projects():
    assert viewer_sees_all_report_projects({"role": "IT отдел"}) is True


@pytest.mark.unit
def test_employee_limited():
    assert viewer_sees_all_report_projects({"role": "Сотрудник"}) is False
