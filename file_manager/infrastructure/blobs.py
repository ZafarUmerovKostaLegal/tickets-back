"""On-disk file storage of the file manager (see backend_common.blob_store).

Files live outside the database as ``<blob_dir>/<sha[:2]>/<sha[2:4]>/<sha256>``; the same content is stored
once. Large uploads arrive in chunks into ``<blob_dir>/tmp/<upload id>.part``. Blocking I/O: call through
``run_in_threadpool`` from async code.
"""

from __future__ import annotations

from pathlib import Path

from backend_common.blob_store import BlobStore, UploadGate

from infrastructure.config import get_settings

store = BlobStore(lambda: get_settings().blob_dir)
gate = UploadGate(writers=6, finishers=2)


def root() -> Path:
    return store.root()


def blob_path(sha: str) -> Path:
    return store.blob_path(sha)


def has_blob(sha: str) -> bool:
    return store.has_blob(sha)


def put_bytes(content: bytes) -> str:
    return store.put_bytes(content)


def adopt(path: Path) -> tuple[str, int]:
    return store.adopt(path)


def read_head(sha: str, size: int = 64 * 1024) -> bytes:
    return store.read_head(sha, size)


def read_all(sha: str) -> bytes:
    return store.read_all(sha)


def free_bytes() -> int:
    return store.free_bytes()


def part_path(upload_id: str) -> Path:
    return store.part_path(upload_id)


def append_chunk(upload_id: str, offset: int, data: bytes) -> int:
    return store.append_chunk(upload_id, offset, data)


def part_size(upload_id: str) -> int:
    return store.part_size(upload_id)


def drop_part(upload_id: str) -> None:
    store.drop_part(upload_id)


def link_secret() -> bytes:
    return store.link_secret("FILE_MANAGER_LINK_SECRET")
