from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from infrastructure.config import get_settings
from infrastructure.database import Base, get_session
from infrastructure.models import FileGuardEventModel
from presentation.api import app
from presentation.deps import get_current_user

TOKEN = "a" * 32


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _meta(**overrides: object) -> str:
    body: dict[str, object] = {
        "clientEventId": uuid.uuid4().hex,
        "host": "PC-1",
        "windowsUser": r"KOSTA\ivan",
        "occurredAt": "2026-10-26T12:00:00+00:00",
        "process": "Telegram.exe",
        "fileName": "a.pdf",
        "filePath": r"C:\Users\ivan\Desktop\a.pdf",
        "kind": "send",
        "status": "blocked",
    }
    body.update(overrides)
    return json.dumps(body)


@pytest.fixture
async def api(tmp_path: Path):
    settings = get_settings()
    previous = {name: getattr(settings, name) for name in (
        "files_dir",
        "agent_token",
        "max_upload_bytes",
        "max_stored_bytes",
        "max_events_per_day",
        "journal_logins",
        "ws_internal_secret",
        "notify_emails",
        "auth_service_url",
        "notifications_service_url",
    )}
    settings.files_dir = str(tmp_path / "files")
    Path(settings.files_dir).mkdir()
    settings.agent_token = TOKEN
    settings.max_upload_bytes = 1024
    settings.max_stored_bytes = 1024
    settings.max_events_per_day = 20
    settings.journal_logins = "aakhmadjonov,zumerov"
    settings.ws_internal_secret = ""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, settings, factory
    app.dependency_overrides.clear()
    for name, value in previous.items():
        setattr(settings, name, value)
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_token_is_required(api):
    client, _, _ = api
    missing = await client.post("/api/v1/file-guard/agent/events", data={"meta": _meta()})
    assert missing.status_code == 401
    wrong = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta()},
        headers={"Authorization": "Bearer " + ("b" * 32)},
    )
    assert wrong.status_code == 401
    settings = get_settings()
    settings.agent_token = "short"
    unconfigured = await client.post("/api/v1/file-guard/agent/events", data={"meta": _meta()}, headers=_auth())
    assert unconfigured.status_code == 503
    settings.agent_token = TOKEN


@pytest.mark.asyncio
async def test_agent_cannot_mark_event_allowed_or_inject_sql(api):
    client, _, factory = api
    rejected = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta(status="allowed", kind="send'; drop table file_guard_events; --")},
        headers=_auth(),
    )
    assert rejected.status_code == 400
    payload = _meta(filePath="C:\\docs\\' OR 1=1 --", note="/* comment */")
    accepted = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": payload},
        headers=_auth(),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["duplicate"] is False
    async with factory() as session:
        rows = list((await session.scalars(select(FileGuardEventModel))).all())
    assert len(rows) == 1
    assert rows[0].status == "blocked"
    assert rows[0].file_path == "C:\\docs\\' OR 1=1 --"


@pytest.mark.asyncio
async def test_upload_stays_inside_storage_and_rejects_oversize(api, tmp_path: Path):
    client, settings, _ = api
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    created = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta()},
        files={"file": ("../../secret.txt", b"hello", "application/octet-stream")},
        headers=_auth(),
    )
    assert created.status_code == 201, created.text
    assert created.json()["storedFile"] is True
    stored = list(Path(settings.files_dir).iterdir())
    assert len(stored) == 1
    assert stored[0].name.isalnum()
    assert stored[0].read_bytes() == b"hello"
    assert secret.read_text(encoding="utf-8") == "TOPSECRET"
    assert not (tmp_path / "secret.txt.bak").exists()

    settings.max_upload_bytes = 4
    huge = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta()},
        files={"file": ("big.bin", b"12345", "application/octet-stream")},
        headers=_auth(),
    )
    assert huge.status_code == 413
    assert len(list(Path(settings.files_dir).iterdir())) == 1


@pytest.mark.asyncio
async def test_same_file_from_another_user_is_kept_once(api):
    client, settings, factory = api
    payload = b"same-file-bytes"
    first = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta(windowsUser=r"KOSTA\ivan", fileName="a.pdf")},
        files={"file": ("a.pdf", payload, "application/octet-stream")},
        headers=_auth(),
    )
    assert first.status_code == 201, first.text
    assert first.json()["duplicate"] is False
    second = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta(
            windowsUser=r"KOSTA\petr",
            host="OTHER-PC",
            fileName="copy.pdf",
            filePath=r"\\server\share\copy.pdf",
        )},
        files={"file": ("copy.pdf", payload, "application/octet-stream")},
        headers=_auth(),
    )
    assert second.status_code == 201, second.text
    assert second.json()["duplicate"] is False
    assert second.json()["storedFile"] is True
    assert second.json()["id"] != first.json()["id"]
    assert len(list(Path(settings.files_dir).iterdir())) == 1
    different = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta(windowsUser=r"KOSTA\petr", fileName="b.pdf")},
        files={"file": ("b.pdf", b"other-file-bytes", "application/octet-stream")},
        headers=_auth(),
    )
    assert different.status_code == 201
    assert different.json()["duplicate"] is False
    async with factory() as session:
        rows = list((await session.scalars(select(FileGuardEventModel))).all())
    by_name = {row.file_name: row for row in rows}
    assert by_name["a.pdf"].stored_name == by_name["copy.pdf"].stored_name
    assert by_name["a.pdf"].stored_name != by_name["b.pdf"].stored_name
    assert by_name["copy.pdf"].file_path == r"\\server\share\copy.pdf"


@pytest.mark.asyncio
async def test_storage_cap_keeps_the_event_without_another_file(api):
    client, settings, _ = api
    settings.max_stored_bytes = 4
    first = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta()},
        files={"file": ("a.bin", b"1234", "application/octet-stream")},
        headers=_auth(),
    )
    assert first.status_code == 201
    assert first.json()["storedFile"] is True
    second = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta()},
        files={"file": ("b.bin", b"5678", "application/octet-stream")},
        headers=_auth(),
    )
    assert second.status_code == 201, second.text
    assert second.json()["storedFile"] is False
    assert len(list(Path(settings.files_dir).iterdir())) == 1


@pytest.mark.asyncio
async def test_daily_event_cap(api):
    client, settings, _ = api
    settings.max_events_per_day = 1
    first = await client.post("/api/v1/file-guard/agent/events", data={"meta": _meta()}, headers=_auth())
    assert first.status_code == 201
    second = await client.post("/api/v1/file-guard/agent/events", data={"meta": _meta()}, headers=_auth())
    assert second.status_code == 429


@pytest.mark.asyncio
async def test_journal_hides_files_from_other_users_and_path_escape(api, tmp_path: Path):
    client, settings, factory = api
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    created = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta()},
        files={"file": ("a.pdf", b"pdf-bytes", "application/pdf")},
        headers=_auth(),
    )
    event_id = created.json()["id"]

    anonymous = await client.get("/api/v1/file-guard/events")
    assert anonymous.status_code == 401
    app.dependency_overrides[get_current_user] = lambda: {"id": 2, "email": "other@kostalegal.com"}
    forbidden = await client.get(f"/api/v1/file-guard/events/{event_id}/file")
    assert forbidden.status_code == 403
    assert b"pdf-bytes" not in forbidden.content

    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "email": "aakhmadjonov@kostalegal.com"}
    listed = await client.get("/api/v1/file-guard/events")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["hasFile"] is True
    assert "stored_name" not in item
    assert "storedName" not in item
    downloaded = await client.get(f"/api/v1/file-guard/events/{event_id}/file")
    assert downloaded.status_code == 200
    assert downloaded.content == b"pdf-bytes"
    assert "attachment" in downloaded.headers["content-disposition"].lower()

    async with factory() as session:
        row = await session.get(FileGuardEventModel, event_id)
        assert row is not None
        row.stored_name = "../secret.txt"
        await session.commit()
    escaped = await client.get(f"/api/v1/file-guard/events/{event_id}/file")
    assert escaped.status_code == 404
    assert b"TOPSECRET" not in escaped.content
    assert secret.read_text(encoding="utf-8") == "TOPSECRET"
    assert Path(settings.files_dir).is_dir()


@pytest.mark.asyncio
async def test_only_journal_user_can_set_send_limit(api):
    client, _, _ = api
    app.dependency_overrides[get_current_user] = lambda: {"id": 2, "email": "other@kostalegal.com"}
    denied = await client.put("/api/v1/file-guard/policies", json={"host": "PC-1", "windowsUser": r"KOSTA\ivan", "dailyLimitMb": 2})
    assert denied.status_code == 403
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "email": "aakhmadjonov@kostalegal.com"}
    saved = await client.put("/api/v1/file-guard/policies", json={"host": "PC-1", "windowsUser": r"KOSTA\ivan", "dailyLimitMb": 2})
    assert saved.status_code == 200
    policy = await client.get("/api/v1/file-guard/agent/policy", params={"host": "PC-1", "windowsUser": r"KOSTA\ivan"}, headers=_auth())
    assert policy.status_code == 200
    assert policy.json()["dailyLimitMb"] == 2
    assert policy.json()["bytesLimit"] == 2 * 1024 * 1024
    assert policy.json()["blockAll"] is False


@pytest.mark.asyncio
async def test_daily_limit_is_megabytes_and_user_can_override(api):
    client, _, factory = api
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "email": "aakhmadjonov@kostalegal.com"}
    shared = await client.put("/api/v1/file-guard/policies", json={"host": "*", "windowsUser": "*", "dailyLimitMb": 1})
    assert shared.status_code == 200
    first = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta()},
        files={"file": ("a.pdf", b"a" * 700_000, "application/octet-stream")},
        headers=_auth(),
    )
    second = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta(fileName="b.pdf", filePath=r"C:\Users\ivan\Desktop\b.pdf")},
        files={"file": ("b.pdf", b"b" * 700_000, "application/octet-stream")},
        headers=_auth(),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    async with factory() as session:
        rows = list((await session.scalars(select(FileGuardEventModel))).all())
    by_name = {row.file_name: row.status for row in rows}
    assert by_name["a.pdf"] == "allowed"
    assert by_name["b.pdf"] == "blocked"
    policy = await client.get(
        "/api/v1/file-guard/agent/policy",
        params={"host": "PC-1", "windowsUser": r"KOSTA\ivan"},
        headers=_auth(),
    )
    assert policy.json()["dailyLimitMb"] == 1
    assert policy.json()["bytesUsedToday"] == 700_000
    personal = await client.put(
        "/api/v1/file-guard/policies",
        json={"host": "PC-1", "windowsUser": r"KOSTA\ivan", "dailyLimitMb": 2},
    )
    assert personal.status_code == 200
    third = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta(fileName="c.pdf", filePath=r"C:\Users\ivan\Desktop\c.pdf")},
        files={"file": ("c.pdf", b"c" * 700_000, "application/octet-stream")},
        headers=_auth(),
    )
    assert third.status_code == 201
    assert third.json()["dailyLimitMb"] == 2
    cleared = await client.put(
        "/api/v1/file-guard/policies",
        json={"host": "PC-1", "windowsUser": r"KOSTA\ivan", "dailyLimitMb": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["removed"] is True
    inherited = await client.get(
        "/api/v1/file-guard/agent/policy",
        params={"host": "PC-1", "windowsUser": r"KOSTA\ivan"},
        headers=_auth(),
    )
    assert inherited.json()["dailyLimitMb"] == 1


@pytest.mark.asyncio
async def test_notification_stays_on_internal_services(api, monkeypatch):
    client, settings, _ = api
    settings.ws_internal_secret = "internal-secret"
    settings.notify_emails = "aakhmadjonov@kostalegal.com"
    settings.auth_service_url = "http://auth.test"
    settings.notifications_service_url = "http://notes.test"
    calls: list[str] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": 7}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            calls.append(url)
            return FakeResponse()

        async def post(self, url, **kwargs):
            calls.append(url)
            description = kwargs["json"]["description"]
            assert "http://evil.example" not in url
            assert "http://evil.example" in description
            assert "\n" not in description
            assert "\r" not in description
            return FakeResponse()

    monkeypatch.setattr("application.notify.httpx.AsyncClient", FakeClient)
    created = await client.post(
        "/api/v1/file-guard/agent/events",
        data={"meta": _meta(resource="http://evil.example/hook\r\nBcc: boss")},
        headers=_auth(),
    )
    assert created.status_code == 201, created.text
    assert calls == [
        "http://auth.test/internal/users/by-email",
        "http://notes.test/notifications/system",
    ]
