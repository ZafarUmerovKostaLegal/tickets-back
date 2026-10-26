from presentation.routes.file_guard_routes import safe_upstream_path
from infrastructure.upstream_auth_context import _incoming_authorization, merge_upstream_headers


def test_proxy_path_rejects_escape():
    assert safe_upstream_path("events") == "events"
    assert safe_upstream_path("agent/events") == "agent/events"
    assert safe_upstream_path("agent/../../health") is None
    assert safe_upstream_path("..%2F..%2Fhealth") is None
    assert safe_upstream_path("agent\\events") == "agent/events"
    assert safe_upstream_path("http://evil.example") is None
    assert safe_upstream_path("@evil.example/events") is None
    assert safe_upstream_path("") is None


def test_browser_session_is_forwarded_as_authorization():
    token = _incoming_authorization.set("Bearer user-jwt")
    try:
        headers = merge_upstream_headers({"accept": "application/json"})
    finally:
        _incoming_authorization.reset(token)
    assert headers is not None
    assert headers["Authorization"] == "Bearer user-jwt"


def test_agent_authorization_is_not_replaced():
    token = _incoming_authorization.set("Bearer agent-token")
    try:
        headers = merge_upstream_headers({"Authorization": "Bearer agent-token"})
    finally:
        _incoming_authorization.reset(token)
    assert headers is not None
    assert headers["Authorization"] == "Bearer agent-token"
