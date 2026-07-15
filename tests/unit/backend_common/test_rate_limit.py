from backend_common.rate_limit import (
    classify_path,
    hit_limit,
    reset_memory_for_tests,
    _parse_limit,
)


def test_parse_limit_minute():
    assert _parse_limit("120/minute", 10, 30) == (120, 60)
    assert _parse_limit("20/min", 10, 30) == (20, 60)


def test_classify_auth_and_reports():
    b, n, w = classify_path("/api/v1/auth/login")
    assert b == "auth"
    assert n >= 1 and w >= 1
    b2, _, _ = classify_path("/api/v1/time-tracking/reports/time")
    assert b2 == "reports"
    b3, _, _ = classify_path("/api/v1/tickets")
    assert b3 == "default"


def test_memory_rate_limit_blocks():
    reset_memory_for_tests()
    key = "test:unit:ip1"
    allowed_last = True
    for _ in range(5):
        allowed_last, remaining, retry = hit_limit(key, limit=3, window_sec=60)
    assert allowed_last is False
    assert remaining == 0
    assert retry >= 1
