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

    current = {"user": ADMIN}
    loop = asyncio.new_event_loop()
    app = FastAPI()
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
