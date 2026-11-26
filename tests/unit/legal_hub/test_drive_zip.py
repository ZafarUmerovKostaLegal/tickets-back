"""ZIP download from the Disk: streamed, rights and size checked before the first byte."""

import asyncio
import io
import os
import zipfile

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest

from support.service_path import ensure_service_in_path
from unit.legal_hub.test_drive_explorer import ADMIN, EMPLOYEE, drive  # noqa: F401  (fixture)


def _zip_names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        return sorted(archive.namelist())


def _set_file(drive, file_id: str, content: bytes, *, sha: str | None = None):
    from sqlalchemy import update

    from infrastructure.database import engine
    from infrastructure.models import LegalHubDriveFileModel

    async def go():
        async with engine.begin() as conn:
            await conn.execute(
                update(LegalHubDriveFileModel).where(LegalHubDriveFileModel.id == file_id)
                .values(content=content, blob_sha=sha, size_bytes=len(content) or 10)
            )

    drive.run(go())


def _file_id(listing: dict, name: str) -> str:
    return next(item["id"] for item in listing["files"] if item["name"] == name)


def test_structure_empty_folders_and_legacy_files(drive):
    case = drive.folder("Дело/Письма")
    drive.folder("Дело/Пусто")
    top = next(f["id"] for f in drive.ok("GET", "/tree")["folders"] if f["name"] == "Дело")
    drive.upload(case, ("Отчёт.pdf", b"%PDF one"), ("notes.txt", b"plain text " * 1000))
    loose_id = _file_id(drive.upload(None, ("Отчёт.pdf", b"%PDF two")), "Отчёт.pdf")
    legacy_id = _file_id(drive.upload(case, ("old.doc", b"placeholder")), "old.doc")
    _set_file(drive, legacy_id, b"bytes kept in the database")

    response = drive.call("POST", "/zip", json={"folderIds": [top], "fileIds": [loose_id]})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert _zip_names(response.content) == sorted([
        "Отчёт.pdf", "Дело/", "Дело/Письма/Отчёт.pdf", "Дело/Письма/notes.txt", "Дело/Письма/old.doc", "Дело/Пусто/",
    ])
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("Дело/Письма/old.doc") == b"bytes kept in the database"
        assert archive.read("Дело/Письма/notes.txt") == b"plain text " * 1000
        assert archive.getinfo("Дело/Письма/notes.txt").compress_size < 2000


def test_duplicate_names_get_numbers(drive):
    a = drive.folder("A")
    first = _file_id(drive.upload(None, ("same.txt", b"1")), "same.txt")
    second = _file_id(drive.upload(a, ("same.txt", b"2")), "same.txt")
    response = drive.call("POST", "/zip", json={"fileIds": [first, second]})
    assert _zip_names(response.content) == ["same (2).txt", "same.txt"]


def test_rights_and_limits_are_checked_before_streaming(drive, monkeypatch):
    import presentation.routes.drive as drive_routes

    secret = drive.folder("Secret")
    drive.upload(secret, ("x.txt", b"x" * 2048))
    drive.ok("PUT", f"/security?folderId={secret}", json={"userId": EMPLOYEE["id"], "level": "deny"})
    drive.as_user(EMPLOYEE)
    denied = drive.call("POST", "/zip", json={"folderIds": [secret]})
    assert denied.status_code in (403, 404)
    assert denied.headers["content-type"].startswith("application/json")
    assert drive.call("POST", "/zip/link", json={"folderIds": [secret]}).status_code in (403, 404)
    drive.as_user(ADMIN)
    assert drive.call("POST", "/zip", json={}).status_code == 400
    monkeypatch.setattr(drive_routes, "MAX_ZIP_BYTES", 1024)
    too_big = drive.call("POST", "/zip", json={"folderIds": [secret]})
    assert too_big.status_code == 400 and "МБ" in too_big.json()["detail"]


def test_files_missing_from_storage_are_listed(drive):
    from infrastructure import blobs

    folder = drive.folder("Lost")
    listing = drive.upload(folder, ("kept.txt", b"kept"), ("gone.txt", b"this blob disappears"))
    _set_file(drive, _file_id(listing, "gone.txt"), b"", sha="f" * 64)
    assert not blobs.store.blob_path("f" * 64).exists()
    response = drive.call("POST", "/zip", json={"folderIds": [folder]})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("Lost/kept.txt") == b"kept"
        note = archive.read("_не_скачано.txt").decode("utf-8")
    assert "Lost/gone.txt" in note


def test_link_carries_the_user_and_rechecks_rights(drive):
    folder = drive.folder("Shared")
    drive.upload(folder, ("a.txt", b"alpha"))
    link = drive.ok("POST", "/zip/link", json={"folderIds": [folder]})
    assert link["fileName"] == "Shared.zip" and link["files"] == 1 and link["sizeBytes"] == 5
    token = link["url"].rsplit("/", 1)[-1]
    got = drive.call("GET", f"/zipdl/{token}")
    assert got.status_code == 200 and _zip_names(got.content) == ["Shared/a.txt"]
    assert drive.call("GET", f"/zipdl/{token}").status_code == 200  # browsers may retry
    assert drive.call("GET", f"/zipdl/{token[:-2]}xx").status_code == 403

    secret = drive.folder("Later secret")
    drive.upload(secret, ("s.txt", b"s"))
    drive.ok("PUT", f"/security?folderId={secret}", json={"userId": EMPLOYEE["id"], "level": "read"})
    drive.as_user(EMPLOYEE)
    late = drive.ok("POST", "/zip/link", json={"folderIds": [secret]})["url"].rsplit("/", 1)[-1]
    drive.as_user(ADMIN)
    drive.ok("PUT", f"/security?folderId={secret}", json={"userId": EMPLOYEE["id"], "level": "deny"})
    assert drive.call("GET", f"/zipdl/{late}").status_code in (403, 404)


def test_link_expires(drive, monkeypatch):
    import time as clock

    folder = drive.folder("Soon")
    drive.upload(folder, ("a.txt", b"a"))
    token = drive.ok("POST", "/zip/link", json={"folderIds": [folder]})["url"].rsplit("/", 1)[-1]
    real = clock.time
    monkeypatch.setattr(clock, "time", lambda: real() + 3600)
    assert drive.call("GET", f"/zipdl/{token}").status_code == 410


def test_memory_stays_flat_on_big_files(tmp_path):
    import tracemalloc

    ensure_service_in_path("legal_hub")
    from infrastructure.zip_stream import ZipItem, stream_zip

    big = tmp_path / "big.bin"
    with big.open("wb") as out:
        for _ in range(48):
            out.write(os.urandom(1024 * 1024))
    items = [
        ZipItem(name="Папка/", is_dir=True),
        ZipItem(name="Папка/big.bin", size=big.stat().st_size, path=big),
        ZipItem(name="Папка/huge-claimed.pdf", size=3 * 1024 ** 3, path=big),
    ]

    async def no_legacy(_id):
        return None

    async def collect():
        count = 0
        target = tmp_path / "out.zip"
        with target.open("wb") as out:
            async for block in stream_zip(items, no_legacy):
                count += 1
                out.write(block)
        return count, target

    tracemalloc.start()
    count, target = asyncio.run(collect())
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 12 * 1024 * 1024, peak
    assert count >= 3
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert archive.getinfo("Папка/big.bin").file_size == 48 * 1024 * 1024
        assert archive.getinfo("Папка/huge-claimed.pdf").file_size == 48 * 1024 * 1024


@pytest.mark.parametrize("name", ["scan.PDF", "photo.jpg", "notes.txt"])
def test_packed_formats_use_fast_compression(name):
    ensure_service_in_path("legal_hub")
    from infrastructure.zip_stream import ZipItem, _info

    info = _info(ZipItem(name=name))
    level = getattr(info, "compress_level", None) or getattr(info, "_compresslevel", None)
    assert level == (6 if name.endswith(".txt") else 1)


def test_download_cut_off_midway_is_clean(tmp_path):
    ensure_service_in_path("legal_hub")
    from infrastructure.zip_stream import ZipItem, stream_zip

    big = tmp_path / "big.bin"
    big.write_bytes(os.urandom(5 * 1024 * 1024))

    async def no_legacy(_id):
        return None

    async def cut():
        stream = stream_zip([ZipItem(name="big.bin", size=big.stat().st_size, path=big)], no_legacy)
        first = await stream.__anext__()
        await stream.aclose()
        return first

    assert asyncio.run(cut())


def test_archive_name_comes_from_the_client_and_is_cleaned(drive):
    folder = drive.folder("Contracts")
    listing = drive.upload(folder, ("a.txt", b"a"), ("b.txt", b"b"))
    ids = [item["id"] for item in listing["files"]]
    link = drive.ok("POST", "/zip/link", json={"fileIds": ids, "name": 'Contracts: "Q4"'})
    assert link["fileName"] == "Contracts_ _Q4_.zip"
    got = drive.call("GET", f"/zipdl/{link['url'].rsplit('/', 1)[-1]}")
    assert "Contracts_%20_Q4_.zip" in got.headers["content-disposition"]
    plain = drive.call("POST", "/zip", json={"fileIds": ids})
    assert "%D0%94%D0%B8%D1%81%D0%BA.zip" in plain.headers["content-disposition"]
