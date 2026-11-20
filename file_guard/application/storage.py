from __future__ import annotations

import re
from pathlib import Path

from backend_common.file_retention import retire_file

_STORED_NAME = re.compile(r"^[a-f0-9]{32}$")


def resolve_stored_file(files_dir: str, stored_name: str | None) -> Path | None:
    """Return a file path inside files_dir, or None when the name can escape it."""
    name = (stored_name or "").strip()
    if not _STORED_NAME.fullmatch(name):
        return None
    root = Path(files_dir).resolve()
    path = (root / name).resolve()
    if path.parent != root:
        return None
    return path


def unlink_stored_file(files_dir: str, stored_name: str) -> int:
    """Take one stored copy out of the journal and return its size.

    The file is never deleted: it moves to files_dir/.deleted and is kept there.

    Names that are not a stored hash, and paths that leave the directory, are ignored.
    """
    name = (stored_name or "").strip()
    if not _STORED_NAME.fullmatch(name):
        return 0
    root = Path(files_dir).resolve()
    path = root / name
    if path.is_symlink():
        path.unlink()
        return 0
    if not path.is_file():
        return 0
    if path.resolve().parent != root:
        return 0
    size = path.stat().st_size
    retire_file(path)
    return size


def directory_size(files_dir: str) -> int:
    root = Path(files_dir)
    if not root.is_dir():
        return 0
    total = 0
    for item in root.iterdir():
        if item.is_file():
            total += item.stat().st_size
    return total
