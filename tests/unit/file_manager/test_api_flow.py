import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
from support.service_path import ensure_service_in_path


@pytest.fixture()
def env(monkeypatch):
    ensure_service_in_path("file_manager")
    from fastapi.testclient import TestClient

    import infrastructure.tt_directory as tt
    import presentation.common as common
    import presentation.routes.admin as admin
    from presentation.api import app
    from presentation.deps import Viewer, get_viewer

    directory = tt.Directory(
        clients={"c1": tt.Client("c1", "Acme LLC", False)},
        matters={
            "m1": tt.Matter("m1", "c1", "Acme v. Beta", "KL-1", False),
            "m2": tt.Matter("m2", "c1", "Acme M&A", "KL-2", False),
        },
    )
    state = {"viewer": Viewer(1, "Partner P", True, False, "Bearer x"), "tt": {"m1"}}

    async def fake_directory(_auth, *, force=False):
        return directory

    async def fake_projects(_auth, _uid):
        return frozenset(state["tt"])

    async def fake_assignees(_auth, _pid):
        return [{"userId": 2, "name": "Lawyer L", "position": "Associate"}]

    monkeypatch.setattr(common, "load_directory", fake_directory)
    monkeypatch.setattr(common, "my_project_ids", fake_projects)
    monkeypatch.setattr(admin, "project_assignees", fake_assignees)
    app.dependency_overrides[get_viewer] = lambda: state["viewer"]
    with TestClient(app) as client:
        yield client, state, Viewer
    app.dependency_overrides.clear()


def test_matter_workflow(env):
    client, state, Viewer = env
    base = "/api/v1/files"

    tree = client.get(f"{base}/clients").json()
    assert tree["viewer"]["canManage"] is True
    assert [m["id"] for m in tree["clients"][0]["matters"]] == ["m2", "m1"]

    browse = client.get(f"{base}/matters/m1/browse").json()
    names = {folder["name"] for folder in browse["folders"]}
    assert "Correspondence" in names and "Emails" in names
    folder_id = next(f["id"] for f in browse["folders"] if f["name"] == "Agreements")

    up = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                     files={"files": ("SPA.docx", b"v1", "application/octet-stream")})
    assert up.status_code == 201, up.text
    doc = up.json()["documents"][0]
    assert doc["docNumber"] == "KL-000001" and doc["version"] == 1

    again = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                        files={"files": ("spa.DOCX", b"v2-longer", "application/octet-stream")})
    assert again.json()["documents"][0]["version"] == 2

    assert client.post(f"{base}/documents/{doc['id']}/checkout").status_code == 200

    state["viewer"] = Viewer(2, "Lawyer L", False, False, "Bearer y")
    locked = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                         files={"files": ("SPA.docx", b"v3", "application/octet-stream")})
    assert locked.status_code == 409
    assert client.get(f"{base}/documents/{doc['id']}/content").content == b"v2-longer"
    assert client.get(f"{base}/documents/{doc['id']}/content?version=1").content == b"v1"
    assert client.get(f"{base}/matters/m2/browse").status_code == 403

    tree = client.get(f"{base}/clients").json()
    assert [m["id"] for m in tree["clients"][0]["matters"]] == ["m1"]
    assert [r["matterId"] for r in tree["requestable"]] == ["m2"]
    req = client.post(f"{base}/access-requests", json={"matterId": "m2", "level": "read", "reason": "deal"})
    assert req.status_code == 201

    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer x")
    assert client.post(f"{base}/documents/{doc['id']}/checkin",
                       data={"comment": "final"}).status_code == 200
    pending = client.get(f"{base}/access-requests").json()["requests"]
    assert len(pending) == 1
    ok = client.post(f"{base}/access-requests/{pending[0]['id']}/decision", json={"approve": True})
    assert ok.json()["request"]["status"] == "approved"
    wall = client.put(f"{base}/matters/m1/security", json={"userId": 2, "userName": "Lawyer L", "level": "deny"})
    assert wall.status_code == 200

    state["viewer"] = Viewer(2, "Lawyer L", False, False, "Bearer y")
    assert client.get(f"{base}/matters/m2/browse").status_code == 200
    assert client.get(f"{base}/matters/m1/browse").status_code == 403
    assert client.get(f"{base}/search?q=spa").json()["documents"] == []

    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer x")
    found = client.get(f"{base}/search?q=KL-1").json()["documents"]
    assert [d["id"] for d in found] == [doc["id"]]
    assert client.delete(f"{base}/folders/{folder_id}").status_code == 200
    trash = client.get(f"{base}/trash").json()["items"]
    assert trash[0]["kind"] == "folder" and trash[0]["documents"] == 1
    assert client.post(f"{base}/trash/folder/{folder_id}/restore").status_code == 200
    assert client.get(f"{base}/documents/{doc['id']}").status_code == 200

    stats = client.get(f"{base}/stats").json()
    assert stats["documents"] == 1 and stats["deniedDay"] >= 2
    security = client.get(f"{base}/matters/m1/security").json()
    assert security["effective"][0]["level"] == "deny"
    audit = client.get(f"{base}/audit?action=denied").json()
    assert audit["total"] >= 2
