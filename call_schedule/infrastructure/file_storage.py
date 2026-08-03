from __future__ import annotations

import re
import uuid
from pathlib import Path

from backend_common.media_path import safe_media_path
from infrastructure.config import get_settings

_SAFE_NAME_RE = re.compile(r"[^\w.\-()+ ]+", re.UNICODE)


def _safe_filename(filename: str) -> str:
    name = (filename or "file").strip() or "file"
    name = name.replace("\\", "_").replace("/", "_")
    name = _SAFE_NAME_RE.sub("_", name)
    return name[:180] or "file"


def save_day_file(day_iso: str, filename: str, content: bytes) -> tuple[str, str]:
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError(f"Размер файла превышает {settings.max_upload_mb} МБ")
    day = (day_iso or "").strip()
    if not day:
        raise ValueError("Не указан день")
    safe = _safe_filename(filename)
    unique = f"{uuid.uuid4().hex}__{safe}"
    rel = f"call_schedule_days/{day}/{unique}"
    path = safe_media_path(settings.media_path, rel)
    if path is None:
        raise ValueError("Некорректный путь медиа")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return rel, safe


def delete_storage_file(storage_key: str) -> None:
    path = safe_media_path(get_settings().media_path, storage_key)
    if path is None:
        return
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
