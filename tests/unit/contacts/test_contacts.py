from __future__ import annotations

import pytest


@pytest.mark.unit
def test_unwrap_user_list_from_items():
    from presentation.routes.colleagues import _unwrap_user_list

    assert _unwrap_user_list([{"id": 1}]) == [{"id": 1}]
    assert _unwrap_user_list({"items": [{"id": 2}]}) == [{"id": 2}]
    assert _unwrap_user_list({"data": [{"id": 3}]}) == [{"id": 3}]
    assert _unwrap_user_list("bad") == []


@pytest.mark.unit
def test_normalize_colleague():
    from presentation.schemas import normalize_colleague

    row = normalize_colleague({"id": 5, "email": "x@y.z", "displayName": "Bob"})
    assert row is not None
    assert row.id == 5
    assert row.display_name == "Bob"


@pytest.mark.unit
def test_employee_label_prefers_display_name():
    from presentation.routes.colleagues import _employee_label
    from presentation.schemas import ColleagueOut

    row = ColleagueOut(id=1, email="a@b.c", display_name="Alice", role="Сотрудник")
    assert _employee_label(row) == "Alice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contacts_health():
    from presentation.api import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data.get("service") == "contacts"
