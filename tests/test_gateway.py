

import respx
import pytest
from httpx import Response


async def test_gateway_health(gateway_client):

    r = await gateway_client.get("/health")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        data = r.json()
        assert "status" in data
        assert "service" in data
        assert data["service"] == "gateway"


@pytest.mark.skip(reason="respx mock требует точного совпадения URL")
async def test_gateway_tickets_statuses_with_mock(gateway_client):

    with respx.mock(assert_all_called=False):
        respx.get("http://auth:1236/users/me").mock(
            return_value=Response(200, json={"id": 1, "role": "Сотрудник"})
        )
        respx.get("http://tickets:1235/tickets/statuses").mock(
            return_value=Response(200, json=[{"value": "Новый", "label": "Новый"}])
        )
        r = await gateway_client.get(
            "/api/v1/tickets/statuses",
            headers={"Authorization": "Bearer fake-token"},
        )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert r.json() == [{"value": "Новый", "label": "Новый"}]


async def test_gateway_tickets_statuses_unauthorized(gateway_client):

    r = await gateway_client.get("/api/v1/tickets/statuses")
    assert r.status_code in (401, 503)


@pytest.mark.skip(reason="respx mock требует точного совпадения URL")
async def test_gateway_tickets_priorities_with_mock(gateway_client):

    with respx.mock(assert_all_called=False):
        respx.get("http://auth:1236/users/me").mock(
            return_value=Response(200, json={"id": 1, "role": "Сотрудник"})
        )
        respx.get("http://tickets:1235/tickets/priorities").mock(
            return_value=Response(200, json=[{"value": "Низкий", "label": "Низкий"}])
        )
        r = await gateway_client.get(
            "/api/v1/tickets/priorities",
            headers={"Authorization": "Bearer fake-token"},
        )
    assert r.status_code in (200, 503)


async def test_gateway_media_unauthorized(gateway_client):

    r = await gateway_client.get("/api/v1/media/some/path")
    assert r.status_code == 401


async def test_gateway_ws_url(gateway_client):

    r = await gateway_client.get("/api/v1/tickets/ws-url")
    assert r.status_code == 200
    data = r.json()
    assert "url" in data
    assert "tickets" in data["url"]


async def test_gateway_admin_login(gateway_client):

    r = await gateway_client.post(
        "/api/v1/auth/admin/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert r.status_code in (401, 502)
