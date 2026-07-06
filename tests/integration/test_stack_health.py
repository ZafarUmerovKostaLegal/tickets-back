from __future__ import annotations

import os

import httpx
import pytest


def _gateway_base() -> str:
    return os.environ.get("E2E_GATEWAY_URL", "http://localhost:1234").rstrip("/")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gateway_live_endpoint():
    base = _gateway_base()
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as client:
            live = await client.get("/live")
            health = await client.get("/health")
    except httpx.HTTPError:
        pytest.skip(f"E2E stack not reachable at {base} (docker compose -f docker-compose.e2e.yml up -d)")

    assert live.status_code == 200
    assert health.status_code in (200, 503)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gateway_ws_url_public():
    base = _gateway_base()
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as client:
            r = await client.get("/api/v1/tickets/ws-url")
    except httpx.HTTPError:
        pytest.skip(f"E2E stack not reachable at {base}")

    assert r.status_code == 200
    assert "url" in r.json()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_contacts_health_via_gateway():
    base = _gateway_base()
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as client:
            r = await client.get("/health/contacts")
    except httpx.HTTPError:
        pytest.skip(f"E2E stack not reachable at {base}")

    assert r.status_code in (200, 502, 503)
