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
    state = {"viewer": Viewer(1, "Partner P", True, False, "Bearer x"), "tt": {"m1"}}

    async def fake_directory(_auth, *, force=False):
        return directory

    async def fake_projects(_auth, _uid):
        return frozenset(state["tt"])

    monkeypatch.setattr(common, "load_directory", fake_directory)
    monkeypatch.setattr(common, "my_project_ids", fake_projects)
    app.dependency_overrides[get_viewer] = lambda: state["viewer"]
    with TestClient(app) as client:
        yield client, state, Viewer
    app.dependency_overrides.clear()


def _emails_folder(client, matter_id):
    folders = client.get(f"{BASE}/matters/{matter_id}/browse").json()["folders"]
    return next(f["id"] for f in folders if f["name"] == "Emails")


def _file(client, matter_id, folder_id, name, content=None, **mail):
    data = {"folderId": folder_id, **mail}
    response = client.post(f"{BASE}/matters/{matter_id}/documents", data=data,
                           files={"files": (name, content or name.encode(), "message/rfc822")})
    assert response.status_code == 201, response.text
    return response.json()["documents"][0]


def test_lookup_finds_where_message_was_filed(env):
    client, state, Viewer = env
    folder = _emails_folder(client, "m1")
    doc = _file(client, "m1", folder, "a.eml", mailMessageId="<ABC@x.com>", mailConversationId="conv-1",
                mailFrom="Ivan <ivan@acme.com>", mailSubject="Hello", mailKind="email")

    found = client.post(f"{BASE}/mail/lookup", json={"messageIds": ["abc@x.com"]}).json()["filings"]
    assert len(found) == 1
    row = found[0]
    assert row["documentId"] == doc["id"] and row["sameMessage"] is True
    assert row["matterName"] == "Acme v. Beta" and row["clientName"] == "Acme LLC"
    # Mail filed into Correspondence/Emails lands in the filer's own subfolder.
    assert [c["name"] for c in row["folderPath"]] == ["Correspondence", "Emails", "Partner P"]

    thread = client.post(f"{BASE}/mail/lookup", json={"messageIds": ["other@x"], "conversationId": "conv-1"}).json()
    assert thread["filings"][0]["sameMessage"] is False

    # A colleague without access to the matter does not learn where it was filed.
    state["viewer"] = Viewer(2, "Lawyer L", False, False, "Bearer y")
    state["tt"] = set()
    assert client.post(f"{BASE}/mail/lookup", json={"messageIds": ["abc@x.com"]}).json()["filings"] == []


def test_upload_without_mail_fields_records_nothing(env):
    client, _, _ = env
    folder = _emails_folder(client, "m1")
    _file(client, "m1", folder, "plain.docx")
    assert client.get(f"{BASE}/mail/suggestions").json()["locations"] == []


def test_suggestions_rank_thread_then_sender_then_domain(env):
    client, _, _ = env
    f1 = _emails_folder(client, "m1")
    f2 = _emails_folder(client, "m2")
    _file(client, "m2", f2, "1.eml", mailMessageId="1@x", mailFrom="petr@acme.com")
    _file(client, "m2", f2, "2.eml", mailMessageId="2@x", mailFrom="anna@acme.com")
    _file(client, "m1", f1, "3.eml", mailMessageId="3@x", mailConversationId="conv-9", mailFrom="ivan@acme.com")

    rows = client.get(f"{BASE}/mail/suggestions", params={"fromEmail": "ivan@acme.com", "conversationId": "conv-9"}).json()
    locations = rows["locations"]
    assert [(r["matterId"], r["reason"]) for r in locations] == [("m1", "conversation"), ("m2", "domain")]
    assert locations[1]["count"] == 2

    generic = client.get(f"{BASE}/mail/suggestions", params={"fromEmail": "someone@gmail.com"}).json()["locations"]
    assert {r["reason"] for r in generic} == {"recent"}


def test_suggestions_skip_deleted_folders(env):
    client, _, _ = env
    folder = _emails_folder(client, "m1")
    _file(client, "m1", folder, "1.eml", mailMessageId="1@x", mailFrom="ivan@acme.com")
    assert client.delete(f"{BASE}/folders/{folder}").status_code == 200
    assert client.get(f"{BASE}/mail/suggestions", params={"fromEmail": "ivan@acme.com"}).json()["locations"] == []


def test_mail_into_emails_goes_to_person_folder_with_subject(env):
    client, state, Viewer = env
    emails = _emails_folder(client, "m1")
    doc = _file(client, "m1", emails, "a.eml", mailMessageId="1@x", mailFrom="ivan@acme.com", mailSubject="  SPA   final ")
    folders = client.get(f"{BASE}/matters/m1/browse").json()["folders"]
    person = next(f for f in folders if f["id"] == doc["folderId"])
    assert person["name"] == "Partner P" and person["parentId"] == emails
    assert doc["description"] == "SPA final"

    state["viewer"] = Viewer(3, "Lawyer Two", True, False, "Bearer z")
    other = _file(client, "m1", emails, "b.eml", mailMessageId="2@x", mailSubject="Hello")
    assert other["folderId"] != doc["folderId"]

    # A specific folder chosen by the user is respected.
    agreements = next(f["id"] for f in folders if f["name"] == "Agreements")
    kept = _file(client, "m1", agreements, "c.eml", mailMessageId="3@x")
    assert kept["folderId"] == agreements

    # Plain uploads into Emails are not rerouted.
    plain = _file(client, "m1", emails, "manual.docx")
    assert plain["folderId"] == emails


def test_organize_sorts_old_letters_and_reads_subjects(env):
    client, _, _ = env
    emails = _emails_folder(client, "m1")
    eml = b"From: a@acme.com\r\nSubject: =?UTF-8?B?0J/RgNC40LLQtdGC?= deal\r\n\r\nbody"
    client.post(f"{BASE}/matters/m1/documents", data={"folderId": emails},
                files={"files": ("2026-11-03_01_old.eml", eml, "message/rfc822")})
    client.post(f"{BASE}/matters/m1/documents", data={"folderId": emails},
                files={"files": ("notes.docx", b"x", "application/octet-stream")})

    result = client.post(f"{BASE}/matters/m1/mail/organize", json={"folderId": emails}).json()
    assert result == {"moved": 1, "folders": ["Partner P"], "subjects": 1}

    browse = client.get(f"{BASE}/matters/m1/browse", params={"folderId": emails}).json()
    assert [d["name"] for d in browse["documents"]] == ["notes.docx"]
    person = next(f["id"] for f in browse["folders"] if f["name"] == "Partner P")
    moved = client.get(f"{BASE}/matters/m1/browse", params={"folderId": person}).json()["documents"]
    assert moved[0]["description"] == "Привет deal"

    again = client.post(f"{BASE}/matters/m1/mail/organize", json={"folderId": emails}).json()
    assert again["moved"] == 0
