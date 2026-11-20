import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from datetime import timedelta

import pytest
from support.service_path import ensure_service_in_path

BASE = "/api/v1/files"


@pytest.fixture()
def env():
    ensure_service_in_path("file_manager")
    from fastapi.testclient import TestClient

    from presentation.api import app
    from presentation.deps import Viewer, get_viewer

    state = {"viewer": Viewer(7, "Iroda T", False, False, "Bearer x")}
    app.dependency_overrides[get_viewer] = lambda: state["viewer"]
    with TestClient(app) as client:
        yield client, state, Viewer
    app.dependency_overrides.clear()


def _beat(client, **extra):
    body = {"clientId": "word-abcdef12", "host": "Word", "platform": "windows", "officeVersion": "16.0.18025",
            "addinVersion": "1.2.0", "authMethod": "sso", "ssoStatus": "ok", **extra}
    return client.post(f"{BASE}/addin/heartbeat", json=body, headers={"User-Agent": "Office", "X-Forwarded-For": "10.0.0.5, 1.1.1.1"})


def test_heartbeat_upserts_and_managers_see_who_is_online(env):
    client, state, Viewer = env
    assert _beat(client).json() == {"ok": True}
    assert _beat(client, host="weird", authMethod="dialog", ssoStatus="failed", ssoError="AADSTS50011 redirect mismatch").json() == {"ok": True}
    assert _beat(client, clientId="outlook-12345678", host="outlook", platform="mac").status_code == 200

    assert client.get(f"{BASE}/admin/addin-clients").status_code == 403

    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer y")
    data = client.get(f"{BASE}/admin/addin-clients").json()
    assert data["summary"] == {"online": 2, "onlineUsers": 1, "users": 1, "installs": 2, "ssoProblems": 1}
    word = next(c for c in data["clients"] if c["clientId"] == "word-abcdef12")
    assert word["host"] == "unknown"
    assert word["authMethod"] == "dialog"
    assert word["ssoError"].startswith("AADSTS50011")
    assert word["ip"] == "10.0.0.5"
    assert word["userName"] == "Iroda T"


def test_quiet_installs_are_offline_and_bad_ids_ignored(env):
    client, state, Viewer = env
    import infrastructure.models as models
    import presentation.routes.addin as addin

    assert _beat(client, clientId="../../etc").json() == {"ok": False}
    assert _beat(client).status_code == 200
    real_now = addin.now
    addin.now = lambda: real_now() + timedelta(minutes=30)
    try:
        state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer y")
        data = client.get(f"{BASE}/admin/addin-clients").json()
    finally:
        addin.now = real_now
    assert data["summary"]["online"] == 0
    assert [c["clientId"] for c in data["clients"]] == ["word-abcdef12"]
    assert models.AddinClientModel.__tablename__ == "fm_addin_clients"
