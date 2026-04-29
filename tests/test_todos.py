

import pytest

from service_path import ensure_service_in_path


@pytest.mark.skip(reason="Требует PostgreSQL")
async def test_todos_health(todos_client):

    r = await todos_client.get("/health")
    assert r.status_code in (200, 503)


def test_calendar_oauth_state_is_signed_and_rejects_tampering(monkeypatch):

    monkeypatch.setenv("MICROSOFT_OAUTH_STATE_SECRET", "todos-state-secret-for-tests")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "")
    ensure_service_in_path("todos")

    from infrastructure.config import get_settings
    from infrastructure.oauth_state import decode_oauth_state, encode_oauth_state, resolve_oauth_state_secret

    get_settings.cache_clear()
    try:
        settings = get_settings()
        secret = resolve_oauth_state_secret(
            settings.microsoft_oauth_state_secret,
            settings.microsoft_client_secret,
        )
        state = encode_oauth_state(42, secret)
        assert decode_oauth_state(state, secret) == 42
        assert decode_oauth_state(f"{state}tampered", secret) is None
    finally:
        get_settings.cache_clear()
