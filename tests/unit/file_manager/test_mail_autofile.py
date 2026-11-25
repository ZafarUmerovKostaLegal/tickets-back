import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import httpx
import pytest
from support.service_path import ensure_service_in_path

BASE = "/api/v1/files"
EML = b"Message-ID: <m1@client.com>\r\nFrom: ivan@client.com\r\nSubject: Contract\r\n\r\nHello"


@pytest.fixture()
def env(monkeypatch):
    ensure_service_in_path("file_manager")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")
    from fastapi.testclient import TestClient

    import infrastructure.config as config
    import infrastructure.graph_mail as graph_mail
    import infrastructure.tt_directory as tt
    import presentation.autofile as autofile
    import presentation.common as common
    from presentation.api import app
    from presentation.deps import Viewer, get_viewer

    config.get_settings.cache_clear()
    graph_mail.reset_token_cache()
    monkeypatch.setattr(autofile, "start_autofile_loop", lambda: None)
    directory = tt.Directory(
        clients={"c1": tt.Client("c1", "Acme LLC", False)},
        matters={"m1": tt.Matter("m1", "c1", "Acme v. Beta", "KL-1", False)},
    )

    async def fake_directory(_auth, *, force=False):
        return directory

    async def fake_projects(_auth, _uid):
        return frozenset({"m1"})

    monkeypatch.setattr(common, "load_directory", fake_directory)
    monkeypatch.setattr(common, "my_project_ids", fake_projects)
    state = {"viewer": Viewer(5, "Iroda T", False, False, "Bearer x", email="iroda@kostalegal.com")}
    app.dependency_overrides[get_viewer] = lambda: state["viewer"]
    with TestClient(app) as client:
        yield client, state, Viewer, autofile
    app.dependency_overrides.clear()
    config.get_settings.cache_clear()


def _graph(messages_in, messages_out, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        if url.endswith("/$value"):
            return httpx.Response(200, content=EML)
        if "/mailFolders/inbox/messages/delta" in url or url.endswith("delta-in"):
            return httpx.Response(200, json={"value": messages_in, "@odata.deltaLink": "https://graph/delta-in"})
        if "/mailFolders/sentitems/messages/delta" in url or url.endswith("delta-out"):
            return httpx.Response(200, json={"value": messages_out, "@odata.deltaLink": "https://graph/delta-out"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    return httpx.MockTransport(handler)


def _msg(mid, sender, to=(), when="2999-01-01T09:00:00Z", subject="Contract"):
    return {
        "id": mid, "internetMessageId": f"<{mid}@client.com>", "conversationId": f"conv-{mid}", "subject": subject,
        "from": {"emailAddress": {"address": sender}},
        "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
        "receivedDateTime": when,
    }


def test_rules_api_validates_scope_and_addresses(env):
    client, state, Viewer, _ = env
    assert client.post(f"{BASE}/mail/rules", json={"match": "@gmail.com", "matterId": "m1"}).status_code == 400
    assert client.post(f"{BASE}/mail/rules", json={"match": "client.com", "matterId": "m1", "scope": "shared"}).status_code == 403
    created = client.post(f"{BASE}/mail/rules", json={"match": "@Client.com", "matterId": "m1"})
    assert created.status_code == 201, created.text
    rule = created.json()["rule"]
    assert rule["match"] == "client.com" and rule["matterName"] == "Acme v. Beta" and rule["mine"]
    again = client.post(f"{BASE}/mail/rules", json={"match": "client.com", "matterId": "m1"}).json()["rule"]
    assert again["id"] == rule["id"]

    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer y", email="p@kostalegal.com")
    shared = client.post(f"{BASE}/mail/rules", json={"match": "acme.com", "matterId": "m1", "scope": "shared"})
    assert shared.status_code == 201
    listing = client.get(f"{BASE}/mail/rules").json()
    assert [r["match"] for r in listing["rules"]] == ["acme.com"]  # someone else's personal rule is invisible
    assert listing["canShare"] is True

    state["viewer"] = Viewer(5, "Iroda T", False, False, "Bearer x", email="iroda@kostalegal.com")
    mine = client.get(f"{BASE}/mail/rules").json()["rules"]
    assert {r["match"]: r["canEdit"] for r in mine} == {"acme.com": False, "client.com": True}
    shared_id = next(r["id"] for r in mine if r["match"] == "acme.com")
    assert client.patch(f"{BASE}/mail/rules/{shared_id}", json={"enabled": False}).status_code == 403
    assert client.patch(f"{BASE}/mail/rules/{rule['id']}", json={"enabled": False}).json()["rule"]["enabled"] is False


def test_autofile_files_new_mail_once_into_the_person_folder(env):
    client, state, _, autofile = env
    assert client.post(f"{BASE}/mail/rules", json={"match": "client.com", "matterId": "m1"}).status_code == 201
    box = client.put(f"{BASE}/mail/autofile", json={"enabled": True}).json()["mailbox"]
    assert box["enabled"] is True and box["email"] == "iroda@kostalegal.com"

    calls: list[str] = []
    transport = _graph(
        [_msg("a1", "ivan@client.com"), _msg("a2", "news@other.org"), _msg("old", "ivan@client.com", when="2000-01-01T00:00:00Z")],
        [_msg("s1", "iroda@kostalegal.com", to=("ceo@client.com",), subject="Re: Contract")],
        calls,
    )
    summary = client.portal.call(lambda: autofile.run_once(transport=transport))
    assert summary == {"mailboxes": 1, "filed": 2, "errors": 0}
    assert sum(1 for url in calls if url.endswith("/$value")) == 2  # unrelated and old mail is never downloaded

    found = client.post(f"{BASE}/mail/lookup", json={"messageIds": ["a1@client.com", "s1@client.com"]}).json()["filings"]
    assert {f["matterId"] for f in found} == {"m1"} and len(found) == 2
    tree = client.get(f"{BASE}/matters/m1/browse").json()["folders"]
    person = next(f for f in tree if f["name"] == "Iroda T")
    emails = next(f for f in tree if f["id"] == person["parentId"])
    assert emails["name"] == "Emails"

    # Next pass continues from the delta link and files nothing twice.
    again = client.portal.call(lambda: autofile.run_once(transport=_graph([_msg("a1", "ivan@client.com")], [], calls)))
    assert again["filed"] == 0
    status = client.get(f"{BASE}/mail/rules").json()
    assert status["mailbox"]["filedTotal"] == 2 and status["mailbox"]["lastError"] is None
    assert status["rules"][0]["hits"] == 2


def test_graph_permission_error_is_shown_to_the_owner(env):
    client, _, _, autofile = env
    client.put(f"{BASE}/mail/autofile", json={"enabled": True})

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(403, json={"error": {"message": "Access is denied"}})

    summary = client.portal.call(lambda: autofile.run_once(transport=httpx.MockTransport(handler)))
    assert summary["errors"] == 1
    assert "Mail.Read" in client.get(f"{BASE}/mail/rules").json()["mailbox"]["lastError"]
    assert client.put(f"{BASE}/mail/autofile", json={"enabled": False}).json()["mailbox"]["enabled"] is False


def test_one_broken_mailbox_does_not_stop_the_others(env):
    client, state, Viewer, autofile = env
    assert client.post(f"{BASE}/mail/rules", json={"match": "client.com", "matterId": "m1"}).status_code == 201
    client.put(f"{BASE}/mail/autofile", json={"enabled": True})
    state["viewer"] = Viewer(1, "Partner P", True, False, "Bearer y", email="p@kostalegal.com")
    assert client.post(f"{BASE}/mail/rules", json={"match": "client.com", "matterId": "m1", "scope": "shared"}).status_code == 201
    client.put(f"{BASE}/mail/autofile", json={"enabled": True})

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2" in url:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if "/users/iroda" in url:
            return httpx.Response(404, json={"error": {"message": "gone"}})
        if url.endswith("/$value"):
            return httpx.Response(200, content=EML)
        if "inbox" in url:
            return httpx.Response(200, json={"value": [_msg("p1", "anna@client.com")], "@odata.deltaLink": "x"})
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": "y"})

    summary = client.portal.call(lambda: autofile.run_once(transport=httpx.MockTransport(handler)))
    assert summary == {"mailboxes": 2, "filed": 1, "errors": 1}


def test_autofile_updates_one_document_per_conversation_and_marks_messages(env):
    client, _, _, autofile = env
    client.post(f"{BASE}/mail/rules", json={"match": "client.com", "matterId": "m1"})
    client.put(f"{BASE}/mail/autofile", json={"enabled": True})
    patches: list[tuple[str, dict]] = []

    def eml(mid: str) -> bytes:
        return (f"Message-ID: <{mid}@client.com>\r\nFrom: ivan@client.com\r\nSubject: Contract\r\n\r\nBody {mid}").encode()

    def transport_for(inbox):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "oauth2/v2.0/token" in url:
                return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
            if request.method == "PATCH":
                import json as _json
                patches.append((url.rsplit("/", 1)[-1], _json.loads(request.content)))
                return httpx.Response(200, json={})
            if url.endswith("/$value"):
                mid = url.rsplit("/", 2)[-2]
                return httpx.Response(200, content=eml(mid))
            if "messages/delta" in url or url.endswith("delta-in") or url.endswith("delta-out"):
                rows = inbox if ("inbox" in url or url.endswith("delta-in")) else []
                return httpx.Response(200, json={"value": rows, "@odata.deltaLink": "https://graph/delta-" + ("in" if rows is inbox else "out")})
            return httpx.Response(404, json={})
        return httpx.MockTransport(handler)

    first = _msg("t1", "ivan@client.com")
    first["conversationId"] = "conv-deal"
    first["categories"] = ["Red"]
    reply = _msg("t2", "ivan@client.com", subject="Re: Contract")
    reply["conversationId"] = "conv-deal"
    other = _msg("t3", "ivan@client.com", subject="Invoice")

    summary = client.portal.call(lambda: autofile.run_once(transport=transport_for([first])))
    assert summary["filed"] == 1
    summary = client.portal.call(lambda: autofile.run_once(transport=transport_for([reply, other])))
    assert summary["filed"] == 2

    found = client.post(f"{BASE}/mail/lookup", json={"messageIds": ["t1@client.com", "t2@client.com", "t3@client.com"]}).json()["filings"]
    doc_of = {row["messageId"]: row["documentId"] for row in found}
    assert doc_of["t1@client.com"] == doc_of["t2@client.com"]  # the reply joined the first letter
    assert doc_of["t3@client.com"] != doc_of["t1@client.com"]  # an unrelated letter stays separate
    thread_doc = doc_of["t2@client.com"]
    detail = client.get(f"{BASE}/documents/{thread_doc}").json()
    assert detail["document"]["version"] == 2 and detail["document"]["description"] == "Re: Contract"
    assert client.get(f"{BASE}/documents/{thread_doc}/content").content == eml("t2")

    assert ("t1", {"categories": ["Red", "Saved to Kosta"]}) in patches
    assert {mid for mid, _ in patches} == {"t1", "t2", "t3"}
