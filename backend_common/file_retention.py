"""Files are never deleted: whatever the app "deletes" is moved aside and kept.

A retired file lands in a hidden ``.deleted`` folder next to it (same volume, so it is a cheap
rename), named ``<UTC timestamp>-<short id>__<original name>``. Nothing removes that folder:
an administrator can always get the file back, whenever it was added or deleted.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_log = logging.getLogger(__name__)

DELETED_DIR = ".deleted"


def retired_path_for(path: Path, when: datetime | None = None) -> Path:
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return path.parent / DELETED_DIR / f"{stamp}-{uuid4().hex[:8]}__{path.name}"


def retire_file(path: Path | str | None) -> Path | None:
    """Move ``path`` into its ``.deleted`` folder instead of deleting it. Returns the new path.

    Missing files are ignored. If the move fails the file simply stays where it is — a file is
    never lost because of an error here.
    """
    if path is None:
        return None
    source = Path(path)
    try:
        if source.is_symlink() or not source.is_file():
            return None
        target = retired_path_for(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, target)
        except OSError:
            shutil.copy2(source, target)
            source.unlink()
        _log.info("file retired (kept): %s -> %s", source, target)
        return target
    except OSError as exc:
        _log.warning("could not retire %s, leaving it in place: %s", source, exc)
        return None
