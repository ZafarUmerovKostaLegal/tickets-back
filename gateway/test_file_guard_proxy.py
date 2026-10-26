from presentation.routes.file_guard_routes import safe_upstream_path


def test_proxy_path_rejects_escape():
    assert safe_upstream_path("events") == "events"
    assert safe_upstream_path("agent/events") == "agent/events"
    assert safe_upstream_path("agent/../../health") is None
    assert safe_upstream_path("..%2F..%2Fhealth") is None
    assert safe_upstream_path("agent\\events") == "agent/events"
    assert safe_upstream_path("http://evil.example") is None
    assert safe_upstream_path("@evil.example/events") is None
    assert safe_upstream_path("") is None
