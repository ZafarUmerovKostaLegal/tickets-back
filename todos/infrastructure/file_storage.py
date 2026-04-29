

import re
import uuid
from pathlib import Path

from infrastructure.config import get_settings


def _max_bytes() -> int:
    return get_settings().max_upload_mb * 1024 * 1024


def _safe_filename(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)[:200]
    return base or "file"


def save_todo_card_file(
    *,
    owner_user_id: int,
    card_id: int,
    original_filename: str,
    content: bytes,
) -> tuple[str, int]:

    if len(content) > _max_bytes():
        raise ValueError(f"File size exceeds {get_settings().max_upload_mb}MB")
    rel_dir = Path("todo_cards") / str(owner_user_id) / str(card_id)
    media_base = Path(get_settings().media_path).resolve()
    target_dir = (media_base / rel_dir).resolve()
    if not str(target_dir).startswith(str(media_base)):
        raise ValueError("Invalid path")
    target_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(original_filename).suffix[:32]
    unique = f"{uuid.uuid4().hex}{ext}"
    path = target_dir / unique
    path.write_bytes(content)
    storage_key = str(path.relative_to(media_base)).replace("\\", "/")
    return storage_key, len(content)
