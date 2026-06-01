

from types import SimpleNamespace

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from application.project_billable_rate_sync import (  # noqa: E402
    _shared_billable_config_changed,
    project_uses_shared_billable,
)


def _proj(**kwargs):
    defaults = {
        "billable_rate_type": "per_project",
        "project_billable_rate_amount": "0.01",
        "currency": "EUR",
        "start_date": None,
        "end_date": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_project_uses_shared_billable_per_project_with_amount():
    assert project_uses_shared_billable(_proj()) is True


def test_shared_billable_config_unchanged_on_budget_only_edit():
    before = _proj(budget_amount="1000")
    after = _proj(budget_amount="2000")
    after.budget_amount = "2000"
    assert _shared_billable_config_changed(before, after) is False


def test_shared_billable_config_changed_when_rate_amount_changes():
    before = _proj(project_billable_rate_amount="0.01")
    after = _proj(project_billable_rate_amount="100")
    assert _shared_billable_config_changed(before, after) is True
