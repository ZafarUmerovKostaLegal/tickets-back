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
    hidden = client.get(f"{base}/matters/m1/browse", params={"folderId": folder_id}).json()
    assert hidden["documents"] == []
    assert next(f for f in hidden["folders"] if f["id"] == folder_id)["documents"] == 0
    assert client.get(f"{base}/documents/{doc['id']}").status_code == 404
    assert client.get(f"{base}/documents/{doc['id']}/content").status_code == 404
    assert client.get(f"{base}/search?q=spa").json()["documents"] == []
    mine = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                       files={"files": ("SPA.docx", b"v2-longer", "application/octet-stream")})
    assert mine.status_code == 201, mine.text
    own = mine.json()["results"][0]
    assert own["status"] == "created" and own["document"]["id"] != doc["id"]
    listed = client.get(f"{base}/matters/m1/browse", params={"folderId": folder_id}).json()["documents"]
    assert [d["id"] for d in listed] == [own["document"]["id"]]
    assert client.delete(f"{base}/folders/{folder_id}").status_code == 403
    assert client.get(f"{base}/matters/m2/browse").status_code == 403

    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer x")
    everything = client.get(f"{base}/matters/m1/browse", params={"folderId": folder_id}).json()["documents"]
    assert {d["id"] for d in everything} == {doc["id"], own["document"]["id"]}
    assert client.get(f"{base}/documents/{own['document']['id']}/content").content == b"v2-longer"

    state["viewer"] = Viewer(2, "Lawyer L", False, False, "Bearer y")

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
    assert trash[0]["kind"] == "folder" and trash[0]["documents"] == 2
    assert client.post(f"{base}/trash/folder/{folder_id}/restore").status_code == 200
    assert client.get(f"{base}/documents/{doc['id']}").status_code == 200

    stats = client.get(f"{base}/stats").json()
    assert stats["documents"] == 2 and stats["deniedDay"] >= 2
    security = client.get(f"{base}/matters/m1/security").json()
    assert security["effective"][0]["level"] == "deny"
    audit = client.get(f"{base}/audit?action=denied").json()
    assert audit["total"] >= 2


def test_folder_access_inheritance_and_temporary_entries(env):
    from datetime import datetime, timedelta, timezone

    client, state, Viewer = env
    base = "/api/v1/files"
    partner = Viewer(1, "Partner P", True, False, "Bearer x")
    lawyer = Viewer(2, "Lawyer L", False, False, "Bearer y")
    outsider = Viewer(3, "Outsider O", False, False, "Bearer z")

    browse = client.get(f"{base}/matters/m1/browse").json()
    corr = next(f["id"] for f in browse["folders"] if f["name"] == "Correspondence")
    emails = next(f["id"] for f in browse["folders"] if f["name"] == "Emails")
    drafts = next(f["id"] for f in browse["folders"] if f["name"] == "Drafts")
    letter = client.post(f"{base}/matters/m1/documents", data={"folderId": emails},
                         files={"files": ("letter.pdf", b"partner letter", "application/pdf")}).json()["documents"][0]

    state["viewer"] = lawyer
    assert client.get(f"{base}/documents/{letter['id']}").status_code == 404
    assert client.get(f"{base}/folders/{corr}/access").status_code == 403

    state["viewer"] = partner
    assert client.put(f"{base}/folders/{corr}/access",
                      json={"userId": 2, "userName": "Lawyer L", "level": "read"}).status_code == 200
    access = client.get(f"{base}/folders/{emails}/access").json()
    row = next(m for m in access["members"] if m["userId"] == 2)
    assert row["inherited"]["folderId"] == corr and row["effective"] == "read" and row["entry"] is None
    assert [d["name"] for d in access["documents"]] == ["letter.pdf"]
    assert row["files"] == {letter["id"]: "read"}
    assert row["fileRules"] == {}
    client.put(f"{base}/documents/{letter['id']}/access", json={"userId": 2, "userName": "Lawyer L", "level": "edit"})
    again = client.get(f"{base}/folders/{emails}/access").json()
    granted = next(m for m in again["members"] if m["userId"] == 2)
    assert granted["fileRules"] == {letter["id"]: "edit"} and granted["files"][letter["id"]] == "edit"
    assert client.delete(f"{base}/documents/{letter['id']}/access/2").status_code == 200
    mx = client.get(f"{base}/matters/m1/access-matrix").json()
    assert mx["folderCells"][f"2:{emails}"] == "read" and mx["folderRules"][f"2:{corr}"]["level"] == "read"
    assert any(d["id"] == letter["id"] and d["folderId"] == emails for d in mx["documents"])
    assert any(p["userId"] == 2 for p in mx["people"])

    state["viewer"] = lawyer
    assert client.get(f"{base}/documents/{letter['id']}").status_code == 200
    assert client.patch(f"{base}/documents/{letter['id']}", json={"description": "x"}).status_code == 403

    state["viewer"] = partner
    assert client.put(f"{base}/folders/{emails}/inherit", json={"inherit": False}).json() == {"inherit": False}
    state["viewer"] = lawyer
    assert client.get(f"{base}/documents/{letter['id']}").status_code == 404

    state["viewer"] = partner
    client.put(f"{base}/folders/{emails}/inherit", json={"inherit": True})
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    client.put(f"{base}/folders/{drafts}/access",
               json={"userId": 2, "userName": "Lawyer L", "level": "edit", "suspendedUntil": soon})
    state["viewer"] = lawyer
    names = {f["name"] for f in client.get(f"{base}/matters/m1/browse").json()["folders"]}
    assert "Drafts" not in names and "Emails" in names
    assert client.get(f"{base}/matters/m1/browse", params={"folderId": drafts}).status_code == 403

    state["viewer"] = outsider
    state["tt"] = set()
    assert client.get(f"{base}/matters/m1/browse").status_code == 403
    state["viewer"] = partner
    later = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    client.put(f"{base}/folders/{emails}/access",
               json={"userId": 3, "userName": "Outsider O", "level": "read", "until": later})
    state["viewer"] = outsider
    guest = client.get(f"{base}/matters/m1/browse").json()
    assert {f["name"] for f in guest["folders"]} == {"Correspondence", "Emails"}
    assert client.get(f"{base}/documents/{letter['id']}").status_code == 200
    assert client.post(f"{base}/matters/m1/documents", data={"folderId": emails},
                       files={"files": ("x.txt", b"x", "text/plain")}).status_code == 403

    state["viewer"] = partner
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    client.put(f"{base}/folders/{emails}/access",
               json={"userId": 3, "userName": "Outsider O", "level": "read", "until": past})
    state["viewer"] = outsider
    assert client.get(f"{base}/matters/m1/browse").status_code == 403


def test_security_overview_and_temporary_matter_rules(env):
    from datetime import datetime, timedelta, timezone

    client, state, Viewer = env
    base = "/api/v1/files"
    browse = client.get(f"{base}/matters/m1/browse").json()
    drafts = next(f["id"] for f in browse["folders"] if f["name"] == "Drafts")
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert client.put(f"{base}/matters/m1/security", json={
        "userId": 2, "userName": "Lawyer L", "level": "edit", "suspendedUntil": soon,
    }).status_code == 200
    client.put(f"{base}/matters/m1/security", json={"userId": 5, "userName": "Guest G", "level": "read", "until": soon})
    client.put(f"{base}/folders/{drafts}/access", json={"userId": 2, "userName": "Lawyer L", "level": "deny"})

    data = client.get(f"{base}/matters/m1/security").json()
    lawyer = next(m for m in data["effective"] if m["userId"] == 2)
    assert lawyer["effective"] == "deny" and lawyer["suspendedUntil"]
    guest = next(m for m in data["effective"] if m["userId"] == 5)
    assert guest["until"] and guest["effective"] == "read"
    folder = next(f for f in data["folders"] if f["id"] == drafts)
    assert folder["entries"] == 1 and folder["deny"] == 1 and folder["inherit"] is True
    assert data["matter"]["closed"] is False and data["pendingRequests"] == 0

    summary = client.get(f"{base}/security/overview").json()["matters"]["m1"]
    assert summary == {"rules": 2, "walls": 0, "temporary": 1, "suspended": 1, "folderRules": 1, "folders": 1,
                       "documents": 0, "pending": 0}

    state["viewer"] = Viewer(2, "Lawyer L", False, False, "Bearer y")
    assert client.get(f"{base}/matters/m1/browse").status_code == 403
    assert client.get(f"{base}/security/overview").status_code == 403


def test_document_access_entries_and_confidential_mode(env):
    from datetime import datetime, timedelta, timezone

    client, state, Viewer = env
    base = "/api/v1/files"
    partner = Viewer(1, "Partner P", True, False, "Bearer x")
    lawyer = Viewer(2, "Lawyer L", False, False, "Bearer y")
    junior = Viewer(3, "Junior J", False, False, "Bearer z")

    def upload(folder_id: str, name: str, body: bytes) -> dict:
        response = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                               files={"files": (name, body, "application/octet-stream")})
        return response.json()["documents"][0]

    def names_in(folder_id: str) -> set[str]:
        rows = client.get(f"{base}/matters/m1/browse", params={"folderId": folder_id}).json()["documents"]
        return {row["name"] for row in rows}

    browse = client.get(f"{base}/matters/m1/browse").json()
    agr = next(f["id"] for f in browse["folders"] if f["name"] == "Agreements")
    spa = upload(agr, "SPA.docx", b"spa")
    memo = upload(agr, "Memo.docx", b"memo")
    client.put(f"{base}/folders/{agr}/access", json={"userId": 3, "userName": "Junior J", "level": "read"})

    state["viewer"] = lawyer
    assert client.get(f"{base}/documents/{spa['id']}/access").status_code == 403
    assert client.get(f"{base}/documents/{spa['id']}").status_code == 404

    state["viewer"] = partner
    assert client.put(f"{base}/documents/{spa['id']}/access",
                      json={"userId": 2, "userName": "Lawyer L", "level": "edit"}).status_code == 200
    assert client.put(f"{base}/documents/{memo['id']}/restricted", json={"restricted": True}).json() == {"restricted": True}
    later = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    client.put(f"{base}/documents/{spa['id']}/access",
               json={"userId": 3, "userName": "Junior J", "level": "deny", "until": later})
    info = client.get(f"{base}/documents/{spa['id']}/access").json()
    by_id = {m["userId"]: m for m in info["members"]}
    assert by_id[2]["effective"] == "edit" and by_id[3]["effective"] == "deny" and by_id[1]["owner"] is True
    assert client.put(f"{base}/documents/{spa['id']}/access",
                      json={"userId": 1, "userName": "Partner P", "level": "deny"}).status_code == 400

    listing = client.get(f"{base}/matters/m1/browse", params={"folderId": agr}).json()["documents"]
    assert next(d for d in listing if d["id"] == memo["id"])["access"]["restricted"] is True

    state["viewer"] = lawyer
    assert client.get(f"{base}/documents/{spa['id']}").status_code == 200
    assert client.patch(f"{base}/documents/{spa['id']}", json={"description": "ok"}).status_code == 200
    assert names_in(agr) == {"SPA.docx"}

    state["viewer"] = junior
    assert names_in(agr) == set()
    assert client.get(f"{base}/documents/{memo['id']}").status_code == 404

    state["viewer"] = partner
    client.put(f"{base}/documents/{memo['id']}/restricted", json={"restricted": False})
    client.delete(f"{base}/documents/{spa['id']}/access/3")
    state["viewer"] = junior
    assert names_in(agr) == {"SPA.docx", "Memo.docx"}
    assert client.patch(f"{base}/documents/{memo['id']}", json={"description": "x"}).status_code == 403

    state["viewer"] = partner
    data = client.get(f"{base}/matters/m1/security").json()
    assert [d["name"] for d in data["documents"]] == ["SPA.docx"]
    assert client.get(f"{base}/security/overview").json()["matters"]["m1"]["documents"] == 1


def test_erase_deletes_for_good_and_frees_unshared_files(env):
    client, state, Viewer = env
    base = "/api/v1/files"
    import infrastructure.blobs as blobs

    folder_id = next(f["id"] for f in client.get(f"{base}/matters/m1/browse").json()["folders"] if f["name"] == "Agreements")

    def upload(name: str, body: bytes) -> dict:
        res = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id, "allowDuplicate": "true"},
                          files={"files": (name, body, "application/octet-stream")})
        assert res.status_code == 201, res.text
        return res.json()["documents"][0]

    gone = upload("gone.pdf", b"unique bytes to erase")
    kept = upload("kept.pdf", b"shared bytes")
    twin = upload("twin.pdf", b"shared bytes")
    sha_of = lambda content: __import__("hashlib").sha256(content).hexdigest()
    assert blobs.has_blob(sha_of(b"unique bytes to erase")) and blobs.has_blob(sha_of(b"shared bytes"))

    for doc in (gone, twin):
        assert client.delete(f"{base}/documents/{doc['id']}").status_code == 200
    trash = {item["id"]: item for item in client.get(f"{base}/trash").json()["items"]}
    assert trash[gone["id"]]["storedBytes"] == len(b"unique bytes to erase")
    assert client.get(f"{base}/stats").json()["trashBytes"] >= len(b"unique bytes to erase")

    state["viewer"] = Viewer(2, "Lawyer L", False, False, "Bearer y")
    assert client.delete(f"{base}/trash/document/{gone['id']}/erase").status_code in (403, 404)

    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer x")
    erased = client.delete(f"{base}/trash/document/{gone['id']}/erase").json()
    assert erased["documents"] == 1 and erased["diskBytes"] == len(b"unique bytes to erase")
    assert not blobs.has_blob(sha_of(b"unique bytes to erase"))
    assert client.get(f"{base}/documents/{gone['id']}").status_code == 404

    assert client.delete(f"{base}/trash/document/{twin['id']}/erase").json()["diskBytes"] == 0
    assert blobs.has_blob(sha_of(b"shared bytes")) 
    assert client.get(f"{base}/documents/{kept['id']}/content").content == b"shared bytes"
    assert {item["id"] for item in client.get(f"{base}/trash").json()["items"]} == set()
    actions = [row["action"] for row in client.get(f"{base}/audit?action=erase").json()["items"]]
    assert actions == ["erase", "erase"]


def _eml_with_attachment() -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "info@kostalegal.com"
    msg["To"] = "zumerov@kostalegal.com"
    msg["Subject"] = "Test"
    msg["Message-ID"] = "<att-test-1@kostalegal.com>"
    msg.set_content("See attached")
    msg.add_attachment(b"%PDF-1.4 contract body", maintype="application", subtype="pdf", filename="contract.pdf")
    return bytes(msg)


def test_email_attachment_is_saved_into_the_matter_once(env):
    client, state, Viewer = env
    base = "/api/v1/files"
    folder_id = next(f["id"] for f in client.get(f"{base}/matters/m1/browse").json()["folders"] if f["name"] == "Agreements")
    letter = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                         files={"files": ("Test.eml", _eml_with_attachment(), "message/rfc822")}).json()["documents"][0]

    preview = client.get(f"{base}/documents/{letter['id']}/email").json()
    assert preview["attachments"][0]["name"] == "contract.pdf" and preview["attachments"][0]["saved"] is None

    saved = client.post(f"{base}/documents/{letter['id']}/email/0/save", json={}).json()
    assert saved["status"] == "created" and saved["folderId"] == folder_id and saved["folderPath"] == "Agreements"
    assert saved["document"]["name"] == "contract.pdf"
    assert client.get(f"{base}/documents/{saved['document']['id']}/content").content == b"%PDF-1.4 contract body"

    again = client.post(f"{base}/documents/{letter['id']}/email/0/save", json={}).json()
    assert again["status"] == "exists" and again["document"]["id"] == saved["document"]["id"]

    info = client.get(f"{base}/documents/{letter['id']}/email").json()["attachments"][0]["saved"]
    assert info["documentId"] == saved["document"]["id"] and info["folderPath"] == "Agreements"
    assert info["savedBy"] == "Partner P" and info["visible"] is True

    state["viewer"] = Viewer(2, "Lawyer L", False, False, "Bearer y")
    state["tt"] = {"m1"}
    hidden = client.get(f"{base}/documents/{letter['id']}/email")
    if hidden.status_code == 200:
        assert hidden.json()["attachments"][0]["saved"]["visible"] is False


def test_a_letter_to_several_people_is_filed_once_per_matter(env):
    client, state, Viewer = env
    base = "/api/v1/files"
    letter = _eml_with_attachment()
    form = {"mailMessageId": "<att-test-1@kostalegal.com>", "mailConversationId": "conv-1", "mailKind": "email",
            "mailFrom": "info@kostalegal.com", "mailSubject": "Test"}

    def file_as(user_id: int, name: str) -> dict:
        state["viewer"] = Viewer(user_id, name, False, False, "Bearer e")
        res = client.post(f"{base}/matters/m1/documents", data=form,
                          files={"files": (f"{name}.eml", letter, "message/rfc822")})
        assert res.status_code == 201, res.text
        return res.json()["results"][0]

    first = file_as(2, "Lawyer L")
    assert first["status"] == "created"
    second = file_as(5, "Iroda T")
    third = file_as(6, "Bekzod K")
    assert second["status"] == "exists" and third["status"] == "exists"
    assert second["document"]["id"] == third["document"]["id"] == first["document"]["id"]

    state["viewer"] = Viewer(5, "Iroda T", False, False, "Bearer e")
    found = client.post(f"{base}/mail/lookup", json={"messageIds": ["att-test-1@kostalegal.com"]}).json()["filings"]
    assert len(found) == 1 and found[0]["documentId"] == first["document"]["id"]
    assert found[0]["visible"] is False and found[0]["filedBy"] == "Lawyer L"

    dragged = client.post(f"{base}/matters/m1/documents", files={"files": ("copy.eml", letter, "message/rfc822")}).json()
    assert dragged["results"][0]["status"] == "exists"

    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer x")
    docs = client.get(f"{base}/search?q=eml").json()["documents"]
    assert len([d for d in docs if d["name"].endswith(".eml")]) == 1


def test_attachment_never_becomes_a_version_of_another_file(env):
    client, state, Viewer = env
    base = "/api/v1/files"
    folder_id = next(f["id"] for f in client.get(f"{base}/matters/m1/browse").json()["folders"] if f["name"] == "Agreements")
    other = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                        files={"files": ("contract.pdf", b"another contract", "application/pdf")}).json()["documents"][0]
    letter = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                         files={"files": ("Test.eml", _eml_with_attachment(), "message/rfc822")}).json()["documents"][0]
    saved = client.post(f"{base}/documents/{letter['id']}/email/0/save", json={}).json()
    assert saved["status"] == "created" and saved["document"]["id"] != other["id"]
    assert saved["document"]["name"] == "contract (2).pdf"
    assert client.get(f"{base}/documents/{other['id']}/content").content == b"another contract"
    again = client.post(f"{base}/documents/{letter['id']}/email/0/save", json={}).json()
    assert again["status"] == "exists" and again["document"]["id"] == saved["document"]["id"]
    preview = client.get(f"{base}/documents/{letter['id']}/email").json()["attachments"][0]
    assert preview["saved"]["documentId"] == saved["document"]["id"]


def test_lookup_hides_other_peoples_thread_letters(env):
    client, state, Viewer = env
    base = "/api/v1/files"

    def file_letter(mid: str, user: tuple[int, str]) -> None:
        state["viewer"] = Viewer(user[0], user[1], False, False, "Bearer e")
        body = _eml_with_attachment().replace(b"att-test-1", mid.encode())
        res = client.post(f"{base}/matters/m1/documents", data={
            "mailMessageId": f"<{mid}@kostalegal.com>", "mailConversationId": "conv-x", "mailKind": "email",
        }, files={"files": (f"{mid}.eml", body, "message/rfc822")})
        assert res.status_code == 201, res.text

    file_letter("first", (2, "Lawyer L"))
    file_letter("private-reply", (2, "Lawyer L"))
    state["viewer"] = Viewer(5, "Iroda T", False, False, "Bearer e")
    found = client.post(f"{base}/mail/lookup", json={"messageIds": ["first@kostalegal.com"], "conversationId": "conv-x"}).json()["filings"]
    assert [row["messageId"] for row in found] == ["first@kostalegal.com"]
    assert found[0]["visible"] is False


def test_letters_cannot_be_checked_out(env):
    client, state, Viewer = env
    base = "/api/v1/files"
    folder_id = next(f["id"] for f in client.get(f"{base}/matters/m1/browse").json()["folders"] if f["name"] == "Agreements")
    letter = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                         files={"files": ("Test.eml", _eml_with_attachment(), "message/rfc822")}).json()["documents"][0]
    refused = client.post(f"{base}/documents/{letter['id']}/checkout")
    assert refused.status_code == 400 and "Письма" in refused.json()["detail"]
    doc = client.post(f"{base}/matters/m1/documents", data={"folderId": folder_id},
                      files={"files": ("Memo.docx", b"memo", "application/octet-stream")}).json()["documents"][0]
    assert client.post(f"{base}/documents/{doc['id']}/checkout").status_code == 200


def test_directory_totals_are_only_for_managers(env):
    client, state, Viewer = env
    base = "/api/v1/files"
    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer x")
    managed = client.get(f"{base}/clients").json()
    assert managed["stats"]["clients"] >= 1 and managed["stats"]["matters"] >= 1
    state["viewer"] = Viewer(2, "Lawyer L", False, False, "Bearer y")
    plain = client.get(f"{base}/clients").json()
    assert "stats" not in plain
    assert plain["viewer"]["canManage"] is False
