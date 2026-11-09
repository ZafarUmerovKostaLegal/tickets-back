import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
from support.service_path import ensure_service_in_path


def test_board_settings_normalize_columns_folders_and_filters():
    ensure_service_in_path("legal_hub")
    from application.board_settings import normalize_columns, normalize_filter, normalize_folders

    columns = normalize_columns({
        "custom": [
            {"id": "court", "title": "  Суд  "},
            {"id": "todo", "title": "Занято"},
            {"id": "court", "title": "Повтор"},
        ],
        "order": ["done", "court", "done", "missing"],
        "collapsed": ["court", "nope"],
    })
    assert columns["custom"] == [{"id": "court", "title": "Суд"}]
    assert columns["order"][0] == "done"
    assert columns["order"][1] == "court"
    assert columns["collapsed"] == ["court"]
    assert "todo" in columns["order"]

    folders = normalize_folders({
        "hideLocked": True,
        "folders": [
            {"id": "one", "name": "Суды", "color": "nope", "projectIds": ["a", "a"], "collapsed": True},
            {"id": "two", "name": "Сделки", "color": "accent", "projectIds": ["a", "b"]},
        ],
    })
    assert folders["hideLocked"] is True
    assert folders["folders"][0]["projectIds"] == ["a"]
    assert folders["folders"][0]["color"] == "slate"
    assert folders["folders"][1]["projectIds"] == ["b"]

    saved = normalize_filter({"scope": "project:abc", "onlyLate": True, "priority": "high"})
    assert saved["scope"] == "project:abc"
    assert saved["onlyLate"] is True
    assert saved["priority"] == "high"


@pytest.mark.asyncio
async def test_board_settings_are_stored_per_user():
    ensure_service_in_path("legal_hub")
    from httpx import ASGITransport, AsyncClient

    from infrastructure.database import Base, engine
    from presentation.api import app
    from presentation.deps import require_user

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    current = {"id": 7}

    async def user():
        return dict(current)

    app.dependency_overrides[require_user] = user
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            empty = await client.get("/api/v1/legal-hub/board")
            assert empty.status_code == 200
            assert empty.json()["folders"]["folders"] == []
            assert empty.json()["filters"] == []

            columns = await client.put("/api/v1/legal-hub/board/columns", json={
                "custom": [{"id": "court", "title": "Суд"}],
                "order": ["court", "todo"],
                "collapsed": ["court"],
            })
            assert columns.status_code == 200, columns.text
            assert columns.json()["custom"] == [{"id": "court", "title": "Суд"}]

            folders = await client.put("/api/v1/legal-hub/board/folders", json={
                "hideLocked": True,
                "folders": [{
                    "id": "fld-1",
                    "name": "Суды",
                    "color": "accent",
                    "projectIds": ["p1"],
                    "collapsed": False,
                    "clientOrder": ["Kosta Legal"],
                }],
                "sidebarOrder": [
                    {"type": "client", "name": "North"},
                    {"type": "folder", "id": "fld-1"},
                ],
                "archivedClients": ["Kosta Legal"],
            })
            assert folders.status_code == 200, folders.text

            filters = await client.put("/api/v1/legal-hub/board/filters", json={
                "scope": "mine",
                "onlyLate": True,
                "priority": "high",
            })
            assert filters.status_code == 200, filters.text

            current["id"] = 8
            other = await client.get("/api/v1/legal-hub/board")
            assert other.json()["columns"]["custom"] == []
            assert other.json()["folders"]["folders"] == []

            current["id"] = 7
            mine = await client.get("/api/v1/legal-hub/board")
            body = mine.json()
            assert body["columns"]["custom"][0]["title"] == "Суд"
            assert body["folders"]["hideLocked"] is True
            assert body["folders"]["folders"][0]["projectIds"] == ["p1"]
            assert body["folders"]["folders"][0]["clientOrder"] == ["Kosta Legal"]
            assert body["folders"]["sidebarOrder"] == [
                {"type": "client", "name": "North"},
                {"type": "folder", "id": "fld-1"},
            ]
            assert body["folders"]["archivedClients"] == ["Kosta Legal"]
            assert body["folders"]["archivedProjects"] == []
            assert body["filters"] == [{
                "scope": "mine",
                "onlyLate": True,
                "onlyUrgent": False,
                "project": "",
                "status": "",
                "stream": "",
                "owner": "",
                "priority": "high",
            }]
    finally:
        app.dependency_overrides.clear()
