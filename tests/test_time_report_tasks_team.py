from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from application.services.reports.time_report_service import _get_group_id, _user_is_contractor


def _proj(currency: str = "USD", client_id: str = "c1"):
    return SimpleNamespace(currency=currency, client_id=client_id, name="Proj")


def test_get_group_id_tasks_includes_project_and_currency() -> None:
    e = SimpleNamespace(task_id="t1", project_id="p1", auth_user_id=7)
    projects = {"p1": _proj("EUR")}
    assert _get_group_id(e, "tasks", projects) == ("t1", "p1", "EUR")


def test_get_group_id_team_user_and_currency() -> None:
    e = SimpleNamespace(task_id="t1", project_id="p1", auth_user_id=42)
    projects = {"p1": _proj("USD")}
    assert _get_group_id(e, "team", projects) == (42, "USD")


def test_user_is_contractor_by_position() -> None:
    u = SimpleNamespace(position="Внешний подрядчик")
    assert _user_is_contractor(u) is True
    assert _user_is_contractor(SimpleNamespace(position="Юрист")) is False
