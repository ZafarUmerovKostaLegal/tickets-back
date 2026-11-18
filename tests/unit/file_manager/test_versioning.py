import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
from support.service_path import ensure_service_in_path

BASE = "/api/v1/files"


@pytest.fixture()
def env(monkeypatch):
    ensure_service_in_path("file_manager")
    from fastapi.testclient import TestClient

    import infrastructure.tt_directory as tt
    import presentation.common as common
    from presentation.api import app
    from presentation.deps import Viewer, get_viewer

    directory = tt.Directory(
        clients={"c1": tt.Client("c1", "Acme LLC", False)},
        matters={
            "m1": tt.Matter("m1", "c1", "Acme v. Beta", "KL-1", False),
            "m2": tt.Matter("m2", "c1", "Acme M&A", "KL-2", False),
        },
    )
    state = {"viewer": Viewer(1, "Partner P", True, False, "Bearer x")}

    async def fake_directory(_auth, *, force=False):
        return directory

    async def fake_projects(_auth, _uid):
        return frozenset({"m1", "m2"})

    monkeypatch.setattr(common, "load_directory", fake_directory)
    monkeypatch.setattr(common, "my_project_ids", fake_projects)
    app.dependency_overrides[get_viewer] = lambda: state["viewer"]
    with TestClient(app) as client:
        yield client, state, Viewer
    app.dependency_overrides.clear()


def _folder(client, name, matter="m1"):
    return next(f["id"] for f in client.get(f"{BASE}/matters/{matter}/browse").json()["folders"] if f["name"] == name)


def _upload(client, folder, name, content, matter="m1", **extra):
    response = client.post(f"{BASE}/matters/{matter}/documents", data={"folderId": folder, **extra},
                           files={"files": (name, content, "application/octet-stream")})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["results"][0]["status"], body["results"][0]["document"]


def test_same_name_versions_only_when_content_changes(env):
    client, _, _ = env
    folder = _folder(client, "Agreements")
    assert _upload(client, folder, "SPA.docx", b"v1")[0] == "created"
    status, doc = _upload(client, folder, "spa.DOCX", b"v1")
    assert (status, doc["version"]) == ("unchanged", 1)
    status, doc = _upload(client, folder, "SPA.docx", b"v2")
    assert (status, doc["version"]) == ("version", 2)


def test_identical_content_under_another_name_is_a_duplicate(env):
    client, _, _ = env
    agreements = _folder(client, "Agreements")
    _, original = _upload(client, agreements, "SPA.docx", b"same bytes")
    status, doc = _upload(client, _folder(client, "Emails"), "SPA copy.docx", b"same bytes")
    assert status == "duplicate" and doc["id"] == original["id"]

    status, doc = _upload(client, agreements, "SPA copy.docx", b"same bytes", allowDuplicate="true")
    assert status == "created" and doc["id"] != original["id"]

    # Another matter is a different file cabinet.
    assert _upload(client, _folder(client, "Agreements", "m2"), "SPA.docx", b"same bytes", matter="m2")[0] == "created"


def test_letter_filed_twice_is_not_copied(env):
    client, state, Viewer = env
    emails = _folder(client, "Emails")
    mail = {"mailMessageId": "<ABC@x>", "mailKind": "email", "mailSubject": "Hi"}
    status, first = _upload(client, emails, "a.eml", b"letter", **mail)
    assert status == "created"

    # A colleague files the same letter (other bytes, other folder, other name).
    state["viewer"] = Viewer(2, "Lawyer L", True, False, "Bearer y")
    status, doc = _upload(client, _folder(client, "Agreements"), "2026 letter.eml", b"rebuilt letter", **mail)
    assert status == "exists" and doc["id"] == first["id"]

    # Its attachment is kept once too.
    att = {"mailMessageId": "abc@x", "mailKind": "attachment"}
    assert _upload(client, emails, "SPA.pdf", b"pdf", **att)[0] == "created"
    assert _upload(client, emails, "SPA.pdf", b"pdf", **att)[0] == "exists"


def test_old_letter_is_matched_by_message_id_header(env):
    client, _, _ = env
    emails = _folder(client, "Emails")
    old = b"Message-ID: <Old-1@mail.acme.com>\r\nSubject: old\r\n\r\nbody"
    _, legacy = _upload(client, emails, "2026-11-03_01_old.eml", old)
    status, doc = _upload(client, emails, "2026-11-03 Ivan - old [x].eml", b"original mime",
                          mailMessageId="old-1@mail.acme.com", mailKind="email")
    assert status == "exists" and doc["id"] == legacy["id"]


def test_thread_file_gets_versions(env):
    client, _, _ = env
    emails = _folder(client, "Emails")
    thread = {"mailConversationId": "conv-1", "mailKind": "thread"}
    status, first = _upload(client, emails, "Conversation_a.eml", b"2 letters", **thread)
    assert status == "created"
    assert _upload(client, emails, "Conversation_a.eml", b"2 letters", **thread)[0] == "unchanged"
    status, doc = _upload(client, emails, "Conversation_renamed.eml", b"3 letters", **thread)
    assert (status, doc["id"], doc["version"]) == ("version", first["id"], 2)


def test_checkin_with_identical_file_adds_no_version(env):
    client, _, _ = env
    _, doc = _upload(client, _folder(client, "Agreements"), "NDA.docx", b"nda")
    assert client.post(f"{BASE}/documents/{doc['id']}/checkout").status_code == 200
    same = client.post(f"{BASE}/documents/{doc['id']}/checkin", files={"file": ("NDA.docx", b"nda")})
    assert same.json()["document"]["version"] == 1
    assert client.post(f"{BASE}/documents/{doc['id']}/checkout").status_code == 200
    changed = client.post(f"{BASE}/documents/{doc['id']}/checkin", files={"file": ("NDA.docx", b"nda v2")})
    assert changed.json()["document"]["version"] == 2


def test_duplicates_report_and_cleanup(env):
    client, _, _ = env
    agreements = _folder(client, "Agreements")
    emails = _folder(client, "Emails")
    _, a = _upload(client, agreements, "A.docx", b"twin")
    _, b = _upload(client, emails, "B.docx", b"twin", allowDuplicate="true")
    _upload(client, emails, "C.docx", b"single")
    _, old = _upload(client, emails, "old.eml", b"Message-ID: <m@x>\r\n\r\n1")
    _, again = _upload(client, agreements, "old copy.eml", b"Message-ID: <m@x>\r\n\r\n2")

    groups = client.get(f"{BASE}/matters/m1/duplicates").json()["groups"]
    by_reason = {g["reason"]: {d["id"] for d in g["documents"]} for g in groups}
    assert by_reason == {"content": {a["id"], b["id"]}, "message": {old["id"], again["id"]}}
    content_group = next(g for g in groups if g["reason"] == "content")
    assert content_group["documents"][0]["id"] == a["id"]
    assert [c["name"] for c in content_group["documents"][0]["folderPath"]] == ["Agreements"]

    assert client.delete(f"{BASE}/documents/{b['id']}").status_code == 200
    groups = client.get(f"{BASE}/matters/m1/duplicates").json()["groups"]
    assert [g["reason"] for g in groups] == ["message"]
