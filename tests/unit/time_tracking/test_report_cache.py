from infrastructure.report_cache import (
    REPORT_CACHE,
    evict_expired_reports,
    get_dim,
    get_report,
    invalidate_all_dims,
    invalidate_all_reports,
    invalidate_dim,
    set_dim,
    set_report,
)


def setup_function():
    invalidate_all_dims()
    invalidate_all_reports()


def test_dim_cache_roundtrip():
    set_dim("users_map", {"1": "a"})
    assert get_dim("users_map") == {"1": "a"}
    invalidate_dim("users_map")
    assert get_dim("users_map") is None


def test_report_cache_keyed_by_params():
    params = {"fn": "get_time_report", "page": 1}
    set_report(params, {"ok": True})
    assert get_report(params) == {"ok": True}
    assert get_report({"fn": "get_time_report", "page": 2}) is None
    invalidate_all_reports()
    assert get_report(params) is None


def test_evict_expired_reports_noop_when_fresh():
    set_report({"x": 1}, 123)
    assert evict_expired_reports() == 0
    assert len(REPORT_CACHE) >= 1
