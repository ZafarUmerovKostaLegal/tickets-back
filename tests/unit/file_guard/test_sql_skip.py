from backend_common.sql_injection_guard import skip_body_sql_scan


def test_sql_body_scan_skips_only_the_agent_ingest_path():
    assert skip_body_sql_scan("/api/v1/file-guard/agent/events") is True
    assert skip_body_sql_scan("/api/v1/file-guard/events") is False
    assert skip_body_sql_scan("/api/v1/file-guard/policies") is False
    assert skip_body_sql_scan("/api/v1/file-guard/events/abc/file") is False
