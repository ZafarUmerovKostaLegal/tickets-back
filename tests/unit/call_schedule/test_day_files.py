from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support.service_path import ensure_service_in_path

ensure_service_in_path("call_schedule")


def _purge_call_schedule_modules() -> None:
    for name in list(sys.modules):
        if (
            name in {"infrastructure", "presentation", "main"}
            or name.startswith("infrastructure.")
            or name.startswith("presentation.")
        ):
            del sys.modules[name]


@pytest.fixture()
def day_files_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'cs.db').as_posix()}")
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    (tmp_path / "media").mkdir(parents=True, exist_ok=True)

    _purge_call_schedule_modules()

    from infrastructure.config import get_settings

    get_settings.cache_clear()

    import asyncio

    from infrastructure.database import Base, engine, async_session_factory, get_session
    import infrastructure.models  # noqa: F401

    async def _prepare() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_prepare())

    from presentation.api import app
    from presentation import deps

    users = {
        1: {"id": 1, "role": "Сотрудник", "email": "a@test"},
        2: {"id": 2, "role": "Сотрудник", "email": "b@test"},
        9: {"id": 9, "role": "Администратор", "email": "admin@test"},
    }
    state = {"user_id": 1}

    async def override_session():
        async with async_session_factory() as session:
            yield session

    async def override_user():
        return users[state["user_id"]]

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[deps.get_current_user] = override_user

    with TestClient(app) as client:
        yield client, state

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    asyncio.get_event_loop().run_until_complete(engine.dispose())
    _purge_call_schedule_modules()


def test_day_files_upload_list_download_delete(day_files_client):
    client, state = day_files_client
    day = "2026-08-03"

    r = client.get(f"/api/v1/call-schedule/days/{day}/files")
    assert r.status_code == 200
    assert r.json() == []

    files = {"file": ("brief.pdf", io.BytesIO(b"%PDF-demo"), "application/pdf")}
    r = client.post(f"/api/v1/call-schedule/days/{day}/files", files=files)
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["originalName"] == "brief.pdf"
    assert meta["sizeBytes"] == 9
    file_id = meta["id"]

    r = client.get(f"/api/v1/call-schedule/days/{day}/files")
    assert len(r.json()) == 1

    r = client.get("/api/v1/call-schedule/days/files-counts", params={"from": "2026-08-01", "to": "2026-08-31"})
    assert r.status_code == 200
    assert r.json()["counts"].get(day) == 1

    state["user_id"] = 2
    r = client.get(f"/api/v1/call-schedule/days/{day}/files/{file_id}/file")
    assert r.status_code == 200
    assert r.content == b"%PDF-demo"

    r = client.delete(f"/api/v1/call-schedule/days/{day}/files/{file_id}")
    assert r.status_code == 403

    state["user_id"] = 9
    r = client.delete(f"/api/v1/call-schedule/days/{day}/files/{file_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get(f"/api/v1/call-schedule/days/{day}/files")
    assert r.json() == []


def test_day_files_author_can_delete(day_files_client):
    client, state = day_files_client
    day = "2026-08-04"
    state["user_id"] = 1
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    meta = client.post(f"/api/v1/call-schedule/days/{day}/files", files=files).json()
    r = client.delete(f"/api/v1/call-schedule/days/{day}/files/{meta['id']}")
    assert r.status_code == 200


def test_day_files_rejects_oversize(day_files_client):
    client, _ = day_files_client
    day = "2026-08-05"
    big = b"x" * (1024 * 1024 + 1)
    files = {"file": ("big.bin", io.BytesIO(big), "application/octet-stream")}
    r = client.post(f"/api/v1/call-schedule/days/{day}/files", files=files)
    assert r.status_code == 400
