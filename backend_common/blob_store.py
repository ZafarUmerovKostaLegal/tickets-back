"""Shared on-disk file storage for services that accept large uploads (file_manager, legal_hub drive).

* ``BlobStore`` — content-addressed files ``<root>/<sha[:2]>/<sha[2:4]>/<sha256>`` plus resumable upload parts
  in ``<root>/tmp``. Blocking I/O: call through ``run_in_threadpool`` from async code.
* ``UploadGate`` — keeps the service alive when many people upload at once from several devices: a bounded
  number of chunk writes / completions run at the same time (memory and disk I/O stay flat), others wait a
  little and then get 503, which clients retry with back-off.
* ``sign_link`` / ``read_link`` — short-lived signed download URLs (the browser downloads straight to disk).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import shutil
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException

READ_BLOCK = 1024 * 1024


class BlobStore:
    def __init__(self, root: Callable[[], str | Path]):
        self._root = root

    def root(self) -> Path:
        path = Path(self._root())
        path.mkdir(parents=True, exist_ok=True)
        return path

    def tmp_dir(self) -> Path:
        path = self.root() / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _valid(sha: str) -> str:
        sha = (sha or "").strip().lower()
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise ValueError("bad blob id")
        return sha

    def blob_path(self, sha: str) -> Path:
        sha = self._valid(sha)
        return self.root() / sha[:2] / sha[2:4] / sha

    def has_blob(self, sha: str) -> bool:
        try:
            return self.blob_path(sha).is_file()
        except ValueError:
            return False

    def _place(self, src: Path, sha: str) -> None:
        dest = self.blob_path(sha)
        if dest.is_file():
            src.unlink(missing_ok=True)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)

    def put_bytes(self, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        if self.has_blob(sha):
            return sha
        tmp = self.tmp_dir() / f"put-{sha}-{os.getpid()}-{id(content)}"
        with open(tmp, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        self._place(tmp, sha)
        return sha

    @staticmethod
    def file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while block := fh.read(READ_BLOCK):
                h.update(block)
        return h.hexdigest()

    def adopt(self, path: Path) -> tuple[str, int]:
        size = path.stat().st_size
        sha = self.file_sha256(path)
        self._place(path, sha)
        return sha, size

    def read_head(self, sha: str, size: int = 64 * 1024) -> bytes:
        try:
            with open(self.blob_path(sha), "rb") as fh:
                return fh.read(size)
        except (OSError, ValueError):
            return b""

    def read_all(self, sha: str) -> bytes:
        with open(self.blob_path(sha), "rb") as fh:
            return fh.read()

    def remove(self, sha: str) -> None:
        try:
            self.blob_path(sha).unlink(missing_ok=True)
        except ValueError:
            pass

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root()).free

    def part_path(self, upload_id: str) -> Path:
        safe = "".join(ch for ch in upload_id if ch.isalnum() or ch == "-")[:64]
        return self.tmp_dir() / f"{safe}.part"

    def append_chunk(self, upload_id: str, offset: int, data: bytes) -> int:
        path = self.part_path(upload_id)
        mode = "r+b" if path.exists() else "wb"
        with open(path, mode) as fh:
            fh.seek(offset)
            fh.write(data)
            fh.truncate(offset + len(data))
            fh.flush()
            os.fsync(fh.fileno())
        return offset + len(data)

    def part_size(self, upload_id: str) -> int:
        path = self.part_path(upload_id)
        return path.stat().st_size if path.exists() else 0

    def drop_part(self, upload_id: str) -> None:
        self.part_path(upload_id).unlink(missing_ok=True)

    def link_secret(self, env_name: str) -> bytes:
        configured = (os.environ.get(env_name) or "").strip()
        if configured:
            return configured.encode()
        path = self.root() / ".link-secret"
        try:
            return path.read_bytes()
        except FileNotFoundError:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(os.urandom(32))
            os.replace(tmp, path)
            return path.read_bytes()


def sign_link(secret: bytes, payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{raw}.{mac}"


def read_link(secret: bytes, token: str) -> dict:
    raw, _, mac = token.partition(".")
    good = hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()[:40]
    if not mac or not hmac.compare_digest(mac, good):
        raise HTTPException(status_code=403, detail="Ссылка недействительна")
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=410, detail="Ссылка устарела — нажмите «Скачать» ещё раз")
    return payload


class UploadGate:
    """Back-pressure for uploads coming from many devices at once."""

    def __init__(self, *, writers: int = 6, finishers: int = 2, wait_seconds: float = 20.0):
        self._writers_n = writers
        self._finishers_n = finishers
        self._wait = wait_seconds
        self._writers: asyncio.Semaphore | None = None
        self._finishers: asyncio.Semaphore | None = None

    def _sem(self, kind: str) -> asyncio.Semaphore:
        if kind == "write":
            if self._writers is None:
                self._writers = asyncio.Semaphore(self._writers_n)
            return self._writers
        if self._finishers is None:
            self._finishers = asyncio.Semaphore(self._finishers_n)
        return self._finishers

    @asynccontextmanager
    async def slot(self, kind: str) -> AsyncIterator[None]:
        sem = self._sem(kind)
        try:
            await asyncio.wait_for(sem.acquire(), timeout=self._wait)
        except TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="Сервер сейчас принимает много файлов — загрузка продолжится автоматически",
                headers={"Retry-After": "5"},
            ) from None
        try:
            yield
        finally:
            sem.release()
