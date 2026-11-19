"""Content-addressed file storage on disk.

Files live outside the database as ``<blob_dir>/<sha[:2]>/<sha[2:4]>/<sha256>``. The same content is
stored once however many documents or versions point to it. Large uploads arrive in chunks into
``<blob_dir>/tmp/<upload id>.part`` and are moved into place when complete, so a multi-gigabyte file
never has to fit in memory.

Everything here is blocking file I/O: call it through ``run_in_threadpool`` from async code.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from infrastructure.config import get_settings

READ_BLOCK = 1024 * 1024


def root() -> Path:
    path = Path(get_settings().blob_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def tmp_dir() -> Path:
    path = root() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _valid(sha: str) -> str:
    sha = (sha or "").strip().lower()
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError("bad blob id")
    return sha


def blob_path(sha: str) -> Path:
    sha = _valid(sha)
    return root() / sha[:2] / sha[2:4] / sha


def has_blob(sha: str) -> bool:
    try:
        return blob_path(sha).is_file()
    except ValueError:
        return False


def _place(src: Path, sha: str) -> None:
    """Move a finished temp file into the store (or drop it when the content is already stored)."""
    dest = blob_path(sha)
    if dest.is_file():
        src.unlink(missing_ok=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)


def put_bytes(content: bytes) -> str:
    """Store small content (regular multipart uploads); returns its sha256."""
    sha = hashlib.sha256(content).hexdigest()
    if has_blob(sha):
        return sha
    tmp = tmp_dir() / f"put-{sha}-{os.getpid()}-{id(content)}"
    with open(tmp, "wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    _place(tmp, sha)
    return sha


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(READ_BLOCK):
            h.update(block)
    return h.hexdigest()


def adopt(path: Path) -> tuple[str, int]:
    """Hash a completed upload and move it into the store; returns (sha256, size)."""
    size = path.stat().st_size
    sha = file_sha256(path)
    _place(path, sha)
    return sha, size


def read_head(sha: str, size: int = 64 * 1024) -> bytes:
    """First bytes of a stored file (enough for e-mail headers)."""
    try:
        with open(blob_path(sha), "rb") as fh:
            return fh.read(size)
    except (OSError, ValueError):
        return b""


def read_all(sha: str) -> bytes:
    """Whole content — only for small files (e-mails); never for user uploads of unknown size."""
    with open(blob_path(sha), "rb") as fh:
        return fh.read()


def free_bytes() -> int:
    return shutil.disk_usage(root()).free


def part_path(upload_id: str) -> Path:
    safe = "".join(ch for ch in upload_id if ch.isalnum() or ch == "-")[:64]
    return tmp_dir() / f"{safe}.part"


def append_chunk(upload_id: str, offset: int, data: bytes) -> int:
    """Write ``data`` at ``offset`` of the upload's temp file; returns the new size."""
    path = part_path(upload_id)
    mode = "r+b" if path.exists() else "wb"
    with open(path, mode) as fh:
        fh.seek(offset)
        fh.write(data)
        fh.truncate(offset + len(data))
        fh.flush()
        os.fsync(fh.fileno())
    return offset + len(data)


def part_size(upload_id: str) -> int:
    path = part_path(upload_id)
    return path.stat().st_size if path.exists() else 0


def drop_part(upload_id: str) -> None:
    part_path(upload_id).unlink(missing_ok=True)


def link_secret() -> bytes:
    """Key for signed download links: FILE_MANAGER_LINK_SECRET, else a random key kept next to the files."""
    configured = (os.environ.get("FILE_MANAGER_LINK_SECRET") or "").strip()
    if configured:
        return configured.encode()
    path = root() / ".link-secret"
    try:
        return path.read_bytes()
    except FileNotFoundError:
        secret = os.urandom(32)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(secret)
        os.replace(tmp, path)
        return path.read_bytes()
