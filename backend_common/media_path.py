"""Safe resolution of paths under a media root (path-traversal containment)."""

from __future__ import annotations

from pathlib import Path


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        return path.is_relative_to(base)
    except AttributeError:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False


def safe_media_path(base: str | Path, relative: str | Path | None) -> Path | None:
    """Resolve ``relative`` under ``base``; return None if outside or invalid.

    Rejects absolute keys and paths that escape the media root after resolve
    (including the classic ``/app/media`` vs ``/app/media_evil`` startswith bug).
    Does not require the file to exist.
    """
    if relative is None:
        return None
    rel_s = str(relative).replace("\\", "/").strip()
    if not rel_s:
        return None
    rel = Path(rel_s)
    if rel.is_absolute():
        return None
    try:
        base_resolved = Path(base).resolve()
        target = (base_resolved / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not _is_relative_to(target, base_resolved):
        return None
    return target


def is_path_under_media(base: str | Path, path: Path) -> bool:
    """True if ``path`` resolves under ``base`` (both may already be absolute)."""
    try:
        base_resolved = Path(base).resolve()
        target = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return _is_relative_to(target, base_resolved)
