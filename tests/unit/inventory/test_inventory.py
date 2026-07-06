

import pytest


@pytest.mark.skip(reason="Требует PostgreSQL")
async def test_inventory_health(inventory_client):

    r = await inventory_client.get("/health")
    assert r.status_code in (200, 503)
