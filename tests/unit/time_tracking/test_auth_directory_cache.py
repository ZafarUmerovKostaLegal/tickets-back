from infrastructure.auth_directory_cache import (
    get_auth_partner_hints,
    get_partners_by_projects,
    invalidate_partner_confirmation_read_caches,
    set_auth_partner_hints,
    set_partners_by_projects,
    should_run_pending_reconcile,
)


def setup_function():
    invalidate_partner_confirmation_read_caches()


def test_auth_partner_hints_cache():
    set_auth_partner_hints("Bearer tok", {1: {"position": "p", "role": "r"}})
    assert get_auth_partner_hints("Bearer tok")[1]["position"] == "p"
    assert get_auth_partner_hints("Bearer other") is None


def test_partners_by_projects_cache():
    pids = ["p2", "p1"]
    set_partners_by_projects(pids, "Bearer a", {"p1": [1], "p2": [2]})
    cached = get_partners_by_projects(["p1", "p2"], "Bearer a")
    assert cached == {"p1": [1], "p2": [2]}


def test_reconcile_throttle():
    assert should_run_pending_reconcile(force=True) is True
    assert should_run_pending_reconcile() is False
