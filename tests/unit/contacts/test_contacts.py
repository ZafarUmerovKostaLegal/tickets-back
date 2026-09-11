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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_internal_extensions_crud_and_duplicate():
    from httpx import ASGITransport, AsyncClient

    from infrastructure.database import Base, engine
    from presentation.api import app
    from presentation.dependencies import get_current_user

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async def admin_user():
        return {"id": 1, "role": "Администратор", "is_archived": False}

    app.dependency_overrides[get_current_user] = admin_user
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/contacts/internal-extensions",
                json={"fullName": "Reception", "extension": "11"},
            )
            assert created.status_code == 201, created.text
            item = created.json()
            assert item["full_name"] == "Reception"
            assert item["extension"] == "11"
            ext_id = item["id"]

            listed = await client.get("/api/v1/contacts/internal-extensions")
            assert listed.status_code == 200
            assert any(row["id"] == ext_id for row in listed.json())

            dup = await client.post(
                "/api/v1/contacts/internal-extensions",
                json={"full_name": "Other", "extension": "11"},
            )
            assert dup.status_code == 409

            patched = await client.patch(
                f"/api/v1/contacts/internal-extensions/{ext_id}",
                json={"full_name": "Front desk", "extension": "110"},
            )
            assert patched.status_code == 200
            assert patched.json()["full_name"] == "Front desk"
            assert patched.json()["extension"] == "110"

            deleted = await client.delete(f"/api/v1/contacts/internal-extensions/{ext_id}")
            assert deleted.status_code == 204

            missing = await client.get("/api/v1/contacts/internal-extensions")
            assert missing.json() == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_internal_extensions_write_forbidden_for_employee():
    from httpx import ASGITransport, AsyncClient

    from infrastructure.database import Base, engine
    from presentation.api import app
    from presentation.dependencies import get_current_user

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def employee():
        return {"id": 2, "role": "Сотрудник", "is_archived": False}

    app.dependency_overrides[get_current_user] = employee
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/v1/contacts/internal-extensions")
            assert listed.status_code == 200
            created = await client.post(
                "/api/v1/contacts/internal-extensions",
                json={"fullName": "Test", "extension": "99"},
            )
            assert created.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_internal_extensions_write_allowed_for_it():
    from httpx import ASGITransport, AsyncClient

    from infrastructure.database import Base, engine
    from presentation.api import app
    from presentation.dependencies import get_current_user

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async def it_user():
        return {"id": 3, "role": "IT отдел", "is_archived": False}

    app.dependency_overrides[get_current_user] = it_user
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/contacts/internal-extensions",
                json={"fullName": "Helpdesk", "extension": "44"},
            )
            assert created.status_code == 201, created.text
            empty = await client.post(
                "/api/v1/contacts/internal-extensions",
                json={"fullName": "   ", "extension": "45"},
            )
            assert empty.status_code == 400
    finally:
        app.dependency_overrides.clear()
