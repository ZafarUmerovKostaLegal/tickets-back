

import pytest
from unittest.mock import AsyncMock


@pytest.mark.unit
@pytest.mark.skip(reason="Требует PostgreSQL")
async def test_auth_health(auth_client):

    r = await auth_client.get("/health")
    assert r.status_code in (200, 503)


@pytest.mark.skip(reason="Требует PostgreSQL")
async def test_auth_roles(auth_client):

    r = await auth_client.get("/auth/roles")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.unit
async def test_auth_admin_login_invalid(auth_client):
    from presentation.routes.auth_routes import get_admin_login_use_case

    mock_uc = AsyncMock()
    mock_uc.execute = AsyncMock(return_value=None)

    async def _override():
        return mock_uc

    app = auth_client._transport.app
    app.dependency_overrides[get_admin_login_use_case] = _override
    try:
        r = await auth_client.post(
            "/auth/admin-login",
            json={"username": "admin", "password": "wrong"},
        )
    finally:
        app.dependency_overrides.pop(get_admin_login_use_case, None)
    assert r.status_code == 401
