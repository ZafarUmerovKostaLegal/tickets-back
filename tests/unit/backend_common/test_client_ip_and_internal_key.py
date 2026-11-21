from types import SimpleNamespace

from backend_common.rate_limit import client_ip
from backend_common.ws_internal_auth import has_valid_internal_ws_key


def _req(peer: str, headers: dict[str, str]):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers={k.lower(): v for k, v in headers.items()})


def test_spoofed_forwarded_for_from_proxy_is_ignored():
    # nginx: X-Real-IP=$remote_addr, XFF=<client value>, <remote_addr>
    r = _req("172.18.0.5", {"X-Forwarded-For": "1.2.3.4, 203.0.113.7", "X-Real-IP": "203.0.113.7"})
    assert client_ip(r) == "203.0.113.7"
    r = _req("172.18.0.5", {"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
    assert client_ip(r) == "203.0.113.7"


def test_direct_public_peer_ignores_headers():
    r = _req("8.8.4.4", {"X-Forwarded-For": "10.0.0.1", "X-Real-IP": "10.0.0.1"})
    assert client_ip(r) == "8.8.4.4"


def test_garbage_header_falls_back_to_peer():
    r = _req("127.0.0.1", {"X-Real-IP": "not-an-ip"})
    assert client_ip(r) == "127.0.0.1"


def test_internal_key_checks():
    assert has_valid_internal_ws_key("s3cret-value", "s3cret-value")
    assert not has_valid_internal_ws_key("s3cret-value", "other")
    assert not has_valid_internal_ws_key("", "")
    assert not has_valid_internal_ws_key("s3cret-value", None)
