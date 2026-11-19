import asyncio
import io
import os
import zipfile

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import httpx
import pytest

from support.service_path import ensure_service_in_path

ADMIN = {"id": 1, "role": "Администратор", "name": "Admin"}
EMPLOYEE = {"id": 2, "role": "Сотрудник", "name": "Employee"}


@pytest.fixture()
def drive():
    ensure_service_in_path("legal_hub")
    from fastapi import FastAPI

    from infrastructure.database import Base, engine
    from infrastructure import models  # noqa: F401
    from presentation.deps import require_user
    from presentation.routes.drive import router
    from presentation.routes.drive_uploads import router as uploads_router
    from presentation.routes.drive_preview import router as preview_router

    current = {"user": ADMIN}
    loop = asyncio.new_event_loop()
    app = FastAPI()
    app.include_router(uploads_router, prefix="/api/v1/legal-hub")
    app.include_router(preview_router, prefix="/api/v1/legal-hub")
    app.include_router(router, prefix="/api/v1/legal-hub")
    app.dependency_overrides[require_user] = lambda: current["user"]

    async def reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    loop.run_until_complete(reset())

    class Client:
        def as_user(self, user):
            current["user"] = user

        def call(self, method, path, **kwargs):
            async def run():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                    return await client.request(method, f"/api/v1/legal-hub/drive{path}", **kwargs)

            return loop.run_until_complete(run())

        def ok(self, method, path, **kwargs):
            response = self.call(method, path, **kwargs)
            assert response.status_code < 300, response.text
            return response.json()

        def folder(self, name, parent=None):
            data = self.ok("POST", "/folders", json={"name": name, "parentId": parent})
            return data["createdId"]

        def upload(self, parent, *items, paths=None):
            files = [("files", (name, content, "application/octet-stream")) for name, content in items]
            data = {"parentId": parent} if parent else {}
            if paths:
                data["paths"] = paths
            return self.ok("POST", "/files", files=files, data=data)

    yield Client()

    async def dispose():
        await engine.dispose()

    loop.run_until_complete(dispose())
    loop.close()


def names(listing):
    return sorted(item["name"] for item in [*listing["folders"], *listing["files"]])


def test_nested_structure_and_duplicate_names(drive):
    leaf = drive.folder("Clients/ACME/2026")
    root = drive.ok("GET", "")
    assert names(root) == ["Clients"]
    assert {f["name"] for f in drive.ok("GET", "/tree")["folders"]} == {"Clients", "ACME", "2026"}
    # Existing levels are reused, the last one gets «(2)» like a new Explorer folder.
    again = drive.folder("Clients/ACME/2026")
    assert again != leaf
    clients = next(f for f in drive.ok("GET", "/tree")["folders"] if f["name"] == "Clients")
    acme_id = next(f["id"] for f in drive.ok("GET", "/tree")["folders"] if f["name"] == "ACME")
    assert names(drive.ok("GET", f"?parentId={acme_id}")) == ["2026", "2026 (2)"]
    assert drive.call("POST", "/folders", json={"name": "bad:name"}).status_code == 400
    assert clients["parentId"] is None


def test_upload_folder_recreates_structure_and_auto_renames(drive):
    drive.upload(None, ("a.txt", b"1"), ("b.txt", b"2"), paths=["Docs/a.txt", "Docs/Sub/b.txt"])
    docs = next(f for f in drive.ok("GET", "")["folders"] if f["name"] == "Docs")
    listing = drive.ok("GET", f"?parentId={docs['id']}")
    assert names(listing) == ["Sub", "a.txt"]
    assert listing["folders"][0]["itemCount"] == 1
    drive.upload(docs["id"], ("a.txt", b"3"))
    assert names(drive.ok("GET", f"?parentId={docs['id']}")) == ["Sub", "a (2).txt", "a.txt"]


def test_rename_conflict_and_hide(drive):
    a = drive.folder("A")
    drive.folder("B")
    assert drive.call("PATCH", f"/folders/{a}", json={"name": "b"}).status_code == 409
    drive.ok("PATCH", f"/folders/{a}", json={"name": "Archive"})
    drive.ok("PATCH", f"/folders/{a}", json={"hidden": True})
    assert names(drive.ok("GET", "")) == ["B"]
    shown = drive.ok("GET", "?showHidden=true")
    assert names(shown) == ["Archive", "B"]
    assert next(f for f in shown["folders"] if f["name"] == "Archive")["hidden"] is True
    assert [f["name"] for f in drive.ok("GET", "/tree")["folders"]] == ["B"]


def test_move_copy_and_cycle_protection(drive):
    src = drive.folder("Src")
    dst = drive.folder("Dst")
    inner = drive.folder("Inner", src)
    listing = drive.upload(src, ("doc.pdf", b"%PDF"))
    file_id = listing["files"][0]["id"]

    assert drive.call("POST", "/move", json={"folderIds": [src], "targetId": inner}).status_code == 400
    drive.ok("POST", "/copy", json={"folderIds": [src], "targetId": dst})
    copied = drive.ok("GET", f"?parentId={dst}")
    assert names(copied) == ["Src"]
    copy_id = copied["folders"][0]["id"]
    assert names(drive.ok("GET", f"?parentId={copy_id}")) == ["Inner", "doc.pdf"]

    drive.ok("POST", "/move", json={"fileIds": [file_id], "targetId": dst})
    assert names(drive.ok("GET", f"?parentId={dst}")) == ["Src", "doc.pdf"]
    drive.ok("POST", "/copy", json={"fileIds": [file_id], "targetId": dst})
    assert names(drive.ok("GET", f"?parentId={dst}")) == ["Src", "doc (2).pdf", "doc.pdf"]


def test_search_is_recursive_and_respects_hidden_and_acl(drive):
    secret = drive.folder("Secret")
    public = drive.folder("Public/Contracts")
    drive.upload(public, ("NDA contract.docx", b"x"))
    drive.upload(secret, ("contract secret.docx", b"x"))
    hits = drive.ok("GET", "/search?q=contract")
    assert sorted(item["name"] for item in hits["files"]) == ["NDA contract.docx", "contract secret.docx"]
    nda = next(item for item in hits["files"] if item["name"].startswith("NDA"))
    assert [p["name"] for p in nda["path"]] == ["Public", "Contracts"]
    assert [f["name"] for f in hits["folders"]] == ["Contracts"]

    drive.ok("PUT", f"/security?folderId={secret}", json={"userId": EMPLOYEE["id"], "level": "deny"})
    drive.as_user(EMPLOYEE)
    hits = drive.ok("GET", "/search?q=contract")
    assert [item["name"] for item in hits["files"]] == ["NDA contract.docx"]
    assert names(drive.ok("GET", "")) == ["Public"]
    assert drive.call("GET", f"?parentId={secret}").status_code == 403


def test_bulk_delete_and_zip(drive):
    folder = drive.folder("Pack/Inner")
    pack = next(f["id"] for f in drive.ok("GET", "/tree")["folders"] if f["name"] == "Pack")
    drive.upload(folder, ("x.txt", b"hello"))
    response = drive.call("POST", "/zip", json={"folderIds": [pack]})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("Pack/Inner/x.txt") == b"hello"
    drive.ok("POST", "/delete", json={"folderIds": [pack]})
    assert names(drive.ok("GET", "")) == []
    assert drive.call("POST", "/delete", json={}).status_code == 400


def test_unique_name_and_path_helpers():
    ensure_service_in_path("legal_hub")
    from presentation.routes.drive import split_path, unique_name

    assert unique_name("Отчёт.pdf", {"отчёт.pdf"}, is_file=True) == "Отчёт (2).pdf"
    assert unique_name("Отчёт (2).pdf", {"отчёт (2).pdf", "отчёт (3).pdf"}, is_file=True) == "Отчёт (4).pdf"
    assert unique_name("Папка", {"папка", "папка (2)"}, is_file=False) == "Папка (3)"
    assert unique_name(".env", {".env"}, is_file=True) == ".env (2)"
    assert split_path(" A / B\\C ") == ["A", "B", "C"]


def _chunked(drive, parent, name, payload, path="", chunk=40_000):
    session = drive.ok("POST", "/uploads", json={"name": name, "size": len(payload), "parentId": parent, "path": path})
    offset = 0
    while offset < len(payload):
        offset = drive.ok("PUT", f"/uploads/{session['id']}", params={"offset": offset}, content=payload[offset:offset + chunk])["received"]
    return drive.ok("POST", f"/uploads/{session['id']}/complete")


def test_large_files_upload_in_chunks_into_folders_and_stream_back(drive):
    payload = os.urandom(150_000)
    root = drive.folder("Projects")
    done = _chunked(drive, root, "Video.mp4", payload, path="Shoot/Day 1/Video.mp4")
    assert done["file"]["sizeBytes"] == len(payload)
    shoot = next(f for f in drive.ok("GET", "", params={"parentId": root})["folders"] if f["name"] == "Shoot")
    assert shoot["id"]
    file_id = done["file"]["id"]
    assert drive.call("GET", f"/files/{file_id}").content == payload
    link = drive.ok("POST", f"/files/{file_id}/link")["url"]
    token = link.rsplit("/", 1)[-1]
    part = drive.call("GET", f"/dl/{token}", headers={"Range": "bytes=5-14"})
    assert part.status_code == 206 and part.content == payload[5:15]
    # Same name again → «(2)», resend of a chunk is harmless, a gap tells where to continue.
    session = drive.ok("POST", "/uploads", json={"name": "Video.mp4", "size": 10, "parentId": done["file"]["folderId"]})
    assert drive.call("PUT", f"/uploads/{session['id']}", params={"offset": 5}, content=b"x").status_code == 409
    drive.ok("PUT", f"/uploads/{session['id']}", params={"offset": 0}, content=b"0123456789")
    drive.ok("PUT", f"/uploads/{session['id']}", params={"offset": 0}, content=b"0123456789")
    assert drive.ok("POST", f"/uploads/{session['id']}/complete")["file"]["name"] == "Video (2).mp4"


def test_copies_share_the_stored_file_and_delete_frees_disk_last(drive):
    from infrastructure import blobs
    import hashlib

    payload = os.urandom(60_000)
    a = drive.folder("A")
    b = drive.folder("B")
    done = _chunked(drive, a, "big.bin", payload)
    drive.ok("POST", "/copy", json={"fileIds": [done["file"]["id"]], "folderIds": [], "targetId": b})
    stored = blobs.store.blob_path(hashlib.sha256(payload).hexdigest())
    drive.ok("POST", "/delete", json={"fileIds": [], "folderIds": [a]})
    assert stored.exists()  # the copy in B still uses it
    drive.ok("POST", "/delete", json={"fileIds": [], "folderIds": [b]})
    assert not stored.exists()


def test_upload_limits_size_space_and_parallel_devices(drive, monkeypatch):
    from infrastructure import blobs
    import presentation.routes.drive_uploads as uploads

    assert drive.call("POST", "/uploads", json={"name": "x.iso", "size": 3 * 1024 ** 3}).status_code == 413
    monkeypatch.setattr(blobs.store, "free_bytes", lambda: 1024)
    full = drive.call("POST", "/uploads", json={"name": "x.iso", "size": 1024 ** 3})
    assert full.status_code == 507 and "места" in full.json()["detail"]
    monkeypatch.undo()
    for index in range(uploads.MAX_ACTIVE_PER_USER):
        drive.ok("POST", "/uploads", json={"name": f"p{index}.bin", "size": 10})
    assert drive.call("POST", "/uploads", json={"name": "more.bin", "size": 10}).status_code == 429


def test_archive_and_email_previews_on_disk(drive):
    from email.message import EmailMessage

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Docs/a.txt", "alpha")
    message = EmailMessage()
    message["Subject"] = "Hello"
    message.set_content("Body")
    message.add_attachment(b"xyz", maintype="text", subtype="plain", filename="n.txt")
    listing = drive.upload(None, ("pack.zip", buffer.getvalue()), ("mail.eml", bytes(message)))
    ids = {item["name"]: item["id"] for item in listing["files"]}
    entries = drive.ok("GET", f"/files/{ids['pack.zip']}/archive")["entries"]
    assert [entry["path"] for entry in entries] == ["Docs/a.txt"]
    assert drive.call("GET", f"/files/{ids['pack.zip']}/archive/0").content == b"alpha"
    token = drive.ok("POST", f"/files/{ids['pack.zip']}/link", params={"inline": True, "entry": 0})["url"].rsplit("/", 1)[-1]
    assert drive.call("GET", f"/dl/{token}").content == b"alpha"
    mail = drive.ok("GET", f"/files/{ids['mail.eml']}/email")
    assert mail["subject"] == "Hello" and mail["attachments"][0]["name"] == "n.txt"
    assert drive.call("GET", f"/files/{ids['mail.eml']}/email/0").content == b"xyz"
