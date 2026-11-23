import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from datetime import datetime, timezone

import httpx
import pytest
from support.service_path import ensure_service_in_path

BASE = "/api/v1/files"
CHAT = "19:meeting_abc@thread.v2"
GRAPH = "https://graph.microsoft.com/v1.0"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000
VTT = (
    "WEBVTT\n\n"
    "0f1e/12-0\n00:00:01.000 --> 00:00:03.000\n<v Ivan Petrov>Добрый день.</v>\n\n"
    "0f1e/13-0\n00:00:03.500 --> 00:00:05.000\n<v Ivan Petrov>Начнём.</v>\n\n"
    "00:01:05.000 --> 00:01:07.000\n<v Iroda T>По договору &amp; срокам.</v>\n"
)


@pytest.fixture()
def env(monkeypatch):
    ensure_service_in_path("file_manager")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")
    from fastapi.testclient import TestClient

    import infrastructure.config as config
    import infrastructure.graph_mail as graph_mail
    import infrastructure.graph_meetings as graph_meetings
    import infrastructure.tt_directory as tt
    import presentation.autofile as autofile
    import presentation.common as common
    import presentation.meetings as meetings
    from presentation.api import app
    from presentation.deps import Viewer, get_viewer

    config.get_settings.cache_clear()
    graph_mail.reset_token_cache()
    monkeypatch.setattr(autofile, "start_autofile_loop", lambda: None)
    monkeypatch.setattr(meetings, "start_meetings_loop", lambda: None)
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

    calls: list[tuple[str, str]] = []
    transport = _graph(calls)

    def routed(fn):
        async def call(*args, **kwargs):
            kwargs.setdefault("transport", transport)
            return await fn(*args, **kwargs)
        return call

    for name in ("chat_members", "meeting_for_chat", "set_record_automatically"):
        monkeypatch.setattr(graph_meetings, name, routed(getattr(graph_meetings, name)))
    state = {"viewer": Viewer(5, "Iroda T", False, False, "Bearer x", email="iroda@kostalegal.com")}
    app.dependency_overrides[get_viewer] = lambda: state["viewer"]
    with TestClient(app) as client:
        yield client, state, Viewer, meetings, transport, calls
    app.dependency_overrides.clear()
    config.get_settings.cache_clear()


def _graph(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append((request.method, url))
        if "oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        if url.endswith("/members"):
            return httpx.Response(200, json={"value": [{"email": "Iroda@KostaLegal.com"}, {"email": "ivan@client.com"}]})
        if "/chats/" in url:
            return httpx.Response(200, json={
                "topic": "Acme: договор",
                "onlineMeetingInfo": {"joinWebUrl": "https://teams.microsoft.com/l/meetup-join/x", "organizer": {"id": "org-1"}},
            })
        if "/onlineMeetings?" in url:
            return httpx.Response(200, json={"value": [{"id": "meet-1", "subject": "Acme", "startDateTime": "2026-11-22T09:30:00Z"}]})
        if request.method == "PATCH":
            return httpx.Response(200, json={})
        if url.endswith("/recordings"):
            return httpx.Response(200, json={"value": [
                {"id": "r1", "createdDateTime": "2026-11-22T10:15:00Z", "recordingContentUrl": f"{GRAPH}/users/org-1/onlineMeetings/meet-1/recordings/r1/content"},
            ]})
        if url.endswith("/recordings/r1/content"):
            return httpx.Response(200, content=MP4)
        if url.endswith("/transcripts"):
            return httpx.Response(200, json={"value": [
                {"id": "t1", "createdDateTime": "2026-11-22T10:15:00Z", "transcriptContentUrl": f"{GRAPH}/users/org-1/onlineMeetings/meet-1/transcripts/t1/content"},
            ]})
        if "/transcripts/t1/content" in url:
            return httpx.Response(200, text=VTT)
        return httpx.Response(404, json={"error": {"message": "nope"}})

    return httpx.MockTransport(handler)


def test_vtt_becomes_readable_text():
    ensure_service_in_path("file_manager")
    from application.meeting_files import recording_file_name, vtt_to_text

    text = vtt_to_text(VTT, "Acme")
    assert text.splitlines() == [
        "Расшифровка встречи: Acme",
        "",
        "[00:00:01] Ivan Petrov: Добрый день. Начнём.",
        "[00:01:05] Iroda T: По договору & срокам.",
    ]
    name = recording_file_name('Acme: "deal"/v2', datetime(2026, 11, 22, 10, 15, tzinfo=timezone.utc))
    assert name == "2026-11-22 15-15 — Запись — Acme deal v2.mp4"


def test_only_participants_link_and_owner_controls_the_link(env):
    client, state, Viewer, *_ = env
    body = {"chatId": CHAT, "matterId": "m1", "autoRecord": True}
    created = client.put(f"{BASE}/meetings/link", json=body)
    assert created.status_code == 200, created.text
    link = created.json()["link"]
    assert link["matterName"] == "Acme v. Beta" and link["topic"] == "Acme: договор" and link["autoRecord"] is True
    assert link["mine"] and link["canEdit"]

    state["viewer"] = Viewer(9, "Outsider", False, False, "Bearer z", email="someone@kostalegal.com")
    assert client.get(f"{BASE}/meetings/link", params={"chatId": CHAT}).status_code == 403
    assert client.put(f"{BASE}/meetings/link", json=body).status_code == 403
    assert client.delete(f"{BASE}/meetings/link", params={"chatId": CHAT}).status_code == 403

    state["viewer"] = Viewer(5, "Iroda T", False, False, "Bearer x", email="iroda@kostalegal.com")
    assert client.get(f"{BASE}/meetings/link", params={"chatId": CHAT}).json()["link"]["id"] == link["id"]


def test_recording_and_transcript_are_filed_once(env):
    client, _, _, meetings, transport, calls = env
    assert client.put(f"{BASE}/meetings/link", json={"chatId": CHAT, "matterId": "m1"}).status_code == 200

    summary = client.portal.call(lambda: meetings.run_once(transport=transport))
    assert summary == {"meetings": 1, "filed": 2, "errors": 0}
    again = client.portal.call(lambda: meetings.run_once(transport=transport))
    assert again == {"meetings": 1, "filed": 0, "errors": 0}
    assert sum(1 for _, url in calls if url.endswith("/recordings/r1/content")) == 1

    docs = client.get(f"{BASE}/matters/m1/browse").json()["documents"]
    names = sorted(doc["name"] for doc in docs)
    assert names == ["2026-11-22 15-15 — Запись — Acme договор.mp4", "2026-11-22 15-15 — Расшифровка — Acme договор.txt"]
    link = client.get(f"{BASE}/meetings/link", params={"chatId": CHAT}).json()["link"]
    assert link["filedTotal"] == 2 and link["lastError"] is None


def test_outlook_tokens_may_use_meeting_links():
    ensure_service_in_path("file_manager")
    from presentation.outlook_scope import outlook_path_allowed

    assert outlook_path_allowed("PUT", "/api/v1/files/meetings/link")
    assert outlook_path_allowed("POST", "/api/v1/files/meetings/link/check")
    assert not outlook_path_allowed("DELETE", "/api/v1/files/meetings")


ZOOM_UUID = "aDYlohsHRtCd4ii1uC2+hA=="
ZOOM_FILES = "https://us06web.zoom.us/rec/download"


def _zoom(calls, participants=("iroda@kostalegal.com",)):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append((request.method, url))
        if url.startswith("https://zoom.us/oauth/token"):
            return httpx.Response(200, json={"access_token": "zoom-app", "expires_in": 3600})
        if url == "https://api.zoom.us/v2/meetings/81234567890":
            return httpx.Response(200, json={"id": 81234567890, "topic": "Acme: zoom call", "start_time": "2026-11-23T09:00:00Z"})
        if "/recordings" in url and "api.zoom.us/v2/meetings/" in url:
            start = "2026-11-23T09:01:00Z"
            return httpx.Response(200, json={"topic": "Acme: zoom call", "host_email": "host@kostalegal.com", "recording_files": [
                {"id": "v1", "file_type": "MP4", "recording_type": "gallery_view", "recording_start": start, "download_url": f"{ZOOM_FILES}/v1", "status": "completed"},
                {"id": "v2", "file_type": "MP4", "recording_type": "shared_screen_with_speaker_view", "recording_start": start, "download_url": f"{ZOOM_FILES}/v2", "status": "completed"},
                {"id": "a1", "file_type": "M4A", "recording_type": "audio_only", "recording_start": start, "download_url": f"{ZOOM_FILES}/a1", "status": "completed"},
                {"id": "t1", "file_type": "TRANSCRIPT", "recording_type": "audio_transcript", "recording_start": start, "download_url": f"{ZOOM_FILES}/t1", "status": "completed"},
            ]})
        if "/past_meetings/" in url:
            return httpx.Response(200, json={"participants": [{"user_email": e} for e in participants], "next_page_token": ""})
        if url.startswith(ZOOM_FILES):
            return httpx.Response(200, content=VTT.encode() if url.endswith("/t1") else MP4)
        return httpx.Response(404, json={"message": "nope"})

    return httpx.MockTransport(handler)


@pytest.fixture()
def zoom_env(env, monkeypatch):
    client, state, Viewer, meetings, _, _ = env
    monkeypatch.setenv("ZOOM_ACCOUNT_ID", "firm")
    monkeypatch.setenv("ZOOM_S2S_CLIENT_ID", "s2s")
    monkeypatch.setenv("ZOOM_S2S_CLIENT_SECRET", "secret")
    import infrastructure.config as config
    import infrastructure.zoom_api as zoom_api

    config.get_settings.cache_clear()
    zoom_api.reset_token_cache()
    calls: list[tuple[str, str]] = []
    holder = {"transport": _zoom(calls)}
    original = zoom_api.meeting

    async def meeting(*args, **kwargs):
        kwargs.setdefault("transport", holder["transport"])
        return await original(*args, **kwargs)

    monkeypatch.setattr(zoom_api, "meeting", meeting)
    yield client, state, Viewer, meetings, holder, calls


def test_zoom_uuid_is_encoded_twice_when_zoom_wants_it():
    ensure_service_in_path("file_manager")
    from infrastructure.zoom_api import meeting_uuid_path

    assert meeting_uuid_path("abc+def==") == "abc%2Bdef%3D%3D"
    assert meeting_uuid_path("/ajXp112QmuoKj4854875==") == "%252FajXp112QmuoKj4854875%253D%253D"


def test_zoom_recording_is_filed_once_with_one_view_and_transcript(zoom_env):
    client, _, _, meetings, holder, calls = zoom_env
    body = {"uuid": ZOOM_UUID, "meetingId": "81234567890", "matterId": "m1"}
    saved = client.put(f"{BASE}/meetings/zoom/link", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["link"]["topic"] == "Acme: zoom call"

    summary = client.portal.call(lambda: meetings.run_once(transport=holder["transport"]))
    assert summary == {"meetings": 1, "filed": 2, "errors": 0}
    downloads = [url for _, url in calls if url.startswith(ZOOM_FILES)]
    assert sorted(url.rsplit("/", 1)[1] for url in downloads) == ["t1", "v2"]
    again = client.portal.call(lambda: meetings.run_once(transport=holder["transport"]))
    assert again["filed"] == 0

    names = sorted(doc["name"] for doc in client.get(f"{BASE}/matters/m1/browse").json()["documents"])
    assert names == ["2026-11-23 14-01 — Запись — Acme zoom call.mp4", "2026-11-23 14-01 — Расшифровка — Acme zoom call.txt"]


def test_zoom_recording_is_not_filed_for_someone_who_was_not_there(zoom_env):
    client, state, Viewer, meetings, holder, calls = zoom_env
    holder["transport"] = _zoom(calls, participants=("other@kostalegal.com",))
    assert client.put(f"{BASE}/meetings/zoom/link", json={"uuid": ZOOM_UUID, "meetingId": "81234567890", "matterId": "m1"}).status_code == 200
    summary = client.portal.call(lambda: meetings.run_once(transport=holder["transport"]))
    assert summary["filed"] == 0
    link = client.get(f"{BASE}/meetings/zoom/link", params={"uuid": ZOOM_UUID}).json()["link"]
    assert link["enabled"] is False and "не участвовал" in link["lastError"]
    assert not [url for _, url in calls if url.startswith(ZOOM_FILES)]

    state["viewer"] = Viewer(9, "Other", False, False, "Bearer z", email="other@kostalegal.com")
    foreign = client.get(f"{BASE}/meetings/zoom/link", params={"uuid": ZOOM_UUID}).json()["link"]
    assert foreign["hidden"] is True and "matterName" not in foreign
    assert client.delete(f"{BASE}/meetings/zoom/link", params={"uuid": ZOOM_UUID}).status_code == 403
