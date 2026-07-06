

import pytest


@pytest.mark.skip(reason="Требует PostgreSQL")
async def test_tickets_health(tickets_client):

    r = await tickets_client.get("/health")
    assert r.status_code in (200, 503)


@pytest.mark.skip(reason="Требует изолированный path")
async def test_tickets_statuses(tickets_client):

    r = await tickets_client.get("/tickets/statuses")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.skip(reason="Требует изолированный path")
async def test_tickets_priorities(tickets_client):

    r = await tickets_client.get("/tickets/priorities")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
