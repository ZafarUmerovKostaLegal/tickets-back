"""Large files: resumable chunked upload straight to disk, streamed download with Range."""

import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from test_versioning import BASE, _folder, _upload, env  # noqa: F401  (fixture)


def _start(client, folder, name, size, **extra):
    response = client.post(f"{BASE}/matters/m1/uploads", json={"name": name, "size": size, "folderId": folder, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _put(client, upload_id, offset, data):
    return client.put(f"{BASE}/uploads/{upload_id}", params={"offset": offset}, content=data)


def test_chunked_upload_resumes_and_lands_as_a_document(env):
    client, _, _ = env
    folder = _folder(client, "Agreements")
    payload = os.urandom(300_000)
    session = _start(client, folder, "Data room.zip", len(payload))
    assert session["received"] == 0 and session["chunkSize"] > 0

    assert _put(client, session["id"], 0, payload[:100_000]).json()["received"] == 100_000
    # The same chunk again (response lost) is harmless; a gap is refused with the real position.
    assert _put(client, session["id"], 0, payload[:100_000]).json()["received"] == 100_000
    gap = _put(client, session["id"], 200_000, payload[200_000:])
    assert gap.status_code == 409 and gap.json()["detail"]["received"] == 100_000
    # Completing early is refused.
    assert client.post(f"{BASE}/uploads/{session['id']}/complete").status_code == 409

    # Resume after a "reconnect".
    assert client.get(f"{BASE}/uploads/{session['id']}").json()["received"] == 100_000
    assert _put(client, session["id"], 100_000, payload[100_000:]).json()["received"] == len(payload)
    done = client.post(f"{BASE}/uploads/{session['id']}/complete")
    assert done.status_code == 200, done.text
    result = done.json()["results"][0]
    assert result["status"] == "created"
    doc_id = result["document"]["id"]

    full = client.get(f"{BASE}/documents/{doc_id}/content")
    assert full.status_code == 200 and full.content == payload
    part = client.get(f"{BASE}/documents/{doc_id}/content", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == payload[10:20]

    # The session is gone; the same bytes again under the same name change nothing.
    assert client.get(f"{BASE}/uploads/{session['id']}").status_code == 404
    again = _start(client, folder, "Data room.zip", len(payload))
    _put(client, again["id"], 0, payload)
    assert client.post(f"{BASE}/uploads/{again['id']}/complete").json()["results"][0]["status"] == "unchanged"


def test_small_uploads_and_new_versions_are_stored_on_disk(env):
    client, _, _ = env
    folder = _folder(client, "Agreements")
    _, doc = _upload(client, folder, "SPA.docx", b"v1")
    session = _start(client, folder, "SPA.docx", 2, documentId=doc["id"])
    _put(client, session["id"], 0, b"v2")
    result = client.post(f"{BASE}/uploads/{session['id']}/complete").json()["results"][0]
    assert (result["status"], result["document"]["version"]) == ("version", 2)
    assert client.get(f"{BASE}/documents/{doc['id']}/content").content == b"v2"
    assert client.get(f"{BASE}/documents/{doc['id']}/content", params={"version": 1}).content == b"v1"


def test_cancel_and_size_limits(env, monkeypatch):
    client, _, _ = env
    folder = _folder(client, "Agreements")
    session = _start(client, folder, "big.bin", 10)
    assert client.delete(f"{BASE}/uploads/{session['id']}").json() == {"ok": True}
    assert client.get(f"{BASE}/uploads/{session['id']}").status_code == 404

    import infrastructure.blobs as blobs
    monkeypatch.setattr(blobs, "free_bytes", lambda: 1024)
    full = client.post(f"{BASE}/matters/m1/uploads", json={"name": "huge.bin", "size": 5 * 1024 ** 3, "folderId": folder})
    assert full.status_code == 507 and "места" in full.json()["detail"]


def test_signed_link_downloads_without_session_and_expires(env, monkeypatch):
    client, _, _ = env
    folder = _folder(client, "Agreements")
    payload = os.urandom(50_000)
    _, doc = _upload(client, folder, "Video.mp4", payload)
    link = client.post(f"{BASE}/documents/{doc['id']}/link").json()
    assert link["url"].startswith("/api/v1/files/dl/") and link["expiresIn"] == 300

    from presentation.api import app
    from presentation.deps import get_viewer
    app.dependency_overrides.pop(get_viewer)  # the browser follows the link without an Authorization header
    full = client.get(link["url"])
    assert full.status_code == 200 and full.content == payload
    assert full.headers["content-disposition"].startswith("attachment;")
    part = client.get(link["url"], headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == payload[:10]

    assert client.get(link["url"][:-2] + "00").status_code == 403
    import presentation.routes.documents as documents
    monkeypatch.setattr(documents.time, "time", lambda: 10 ** 12)
    assert client.get(link["url"]).status_code == 410


def test_purging_trash_removes_the_file_from_disk_unless_shared(env):
    client, _, _ = env
    import infrastructure.blobs as blobs
    from presentation.versioning import digest

    folder = _folder(client, "Agreements")
    _, solo = _upload(client, folder, "Solo.pdf", b"only here")
    _, first = _upload(client, folder, "Shared.pdf", b"shared bytes")
    _upload(client, folder, "Shared copy.pdf", b"shared bytes", allowDuplicate="true")
    for doc in (solo, first):
        assert client.delete(f"{BASE}/documents/{doc['id']}").status_code == 200
        assert client.delete(f"{BASE}/trash/document/{doc['id']}").status_code == 200
    assert not blobs.blob_path(digest(b"only here")).exists()
    assert blobs.blob_path(digest(b"shared bytes")).exists()
