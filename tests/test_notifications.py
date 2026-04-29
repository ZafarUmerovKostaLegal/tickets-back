

import pytest
from backend_common.ws_internal_auth import has_valid_internal_ws_key


@pytest.mark.skip(reason="Требует PostgreSQL")
async def test_notifications_health(notifications_client):

    r = await notifications_client.get("/health")
    assert r.status_code in (200, 503)


def test_internal_ws_key_requires_non_empty_secret_and_header():

    assert has_valid_internal_ws_key("", "abc") is False
    assert has_valid_internal_ws_key("expected", None) is False
    assert has_valid_internal_ws_key("expected", "wrong") is False
    assert has_valid_internal_ws_key("expected", "expected") is True
