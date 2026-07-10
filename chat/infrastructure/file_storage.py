import re
import uuid
from pathlib import Path

from backend_common.media_path import safe_media_path
from infrastructure.config import get_settings


def _max_bytes() -> int:
    return get_settings().max_file_bytes


def _media_base() -> Path:
    return Path(get_settings().media_path).resolve()


def _safe_filename(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)[:200]
    return base or "file"


def save_chat_file(
    *,
    room_id: int,
    message_id: int,
    original_filename: str,
    content: bytes,
) -> tuple[str, int]:
    if not content:
        raise ValueError("Empty file")
    if len(content) > _max_bytes():
        mb = _max_bytes() // (1024 * 1024)
        raise ValueError(f"File size exceeds {mb}MB")
    rel_dir = Path("chat_attachments") / str(room_id) / str(message_id)
    media_base = _media_base()
    target_dir = safe_media_path(media_base, rel_dir)
    if target_dir is None:
        raise ValueError("Invalid path")
    target_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(original_filename or "").suffix[:32]
    unique = f"{uuid.uuid4().hex}{ext}"
    path = target_dir / unique
    path.write_bytes(content)
    storage_key = str(path.relative_to(media_base)).replace("\\", "/")
    return storage_key, len(content)


def resolve_storage_path(storage_key: str) -> Path | None:
    if not storage_key:
        return None
    path = safe_media_path(_media_base(), storage_key)
    if path is None or not path.is_file():
        return None
    return path
