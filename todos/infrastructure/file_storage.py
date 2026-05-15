

import re
import uuid
from pathlib import Path

from infrastructure.config import get_settings


BOARD_BACKGROUND_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


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


def image_magic_matches_content(content: bytes, ext: str) -> bool:
    e = ext.lower()
    if e in {".jpg", ".jpeg"}:
        return content.startswith(b"\xff\xd8\xff")
    if e == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if e == ".gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if e == ".webp":
        return content[:4] == b"RIFF" and len(content) >= 12 and content[8:12] == b"WEBP"
    return False


def save_todo_board_background(
    *,
    owner_user_id: int,
    board_id: int,
    original_filename: str,
    content: bytes,
) -> tuple[str, int]:
    if len(content) > _max_bytes():
        raise ValueError(f"File size exceeds {get_settings().max_upload_mb}MB")

    ext = (Path(original_filename or "").suffix or "").lower()
    if ext not in BOARD_BACKGROUND_ALLOWED_EXTENSIONS:
        raise ValueError(
            "Invalid image format. Allowed: "
            + ", ".join(sorted(BOARD_BACKGROUND_ALLOWED_EXTENSIONS))
        )
    if not image_magic_matches_content(content, ext):
        raise ValueError("File content does not match allowed image format")

    rel_dir = Path("todo_board_backgrounds") / str(owner_user_id) / str(board_id)
    media_base = Path(get_settings().media_path).resolve()
    target_dir = (media_base / rel_dir).resolve()
    if not str(target_dir).startswith(str(media_base)):
        raise ValueError("Invalid path")
    target_dir.mkdir(parents=True, exist_ok=True)

    unique = f"{uuid.uuid4().hex}{ext}"
    path = target_dir / unique
    path.write_bytes(content)
    storage_key = str(path.relative_to(media_base)).replace("\\", "/")
    return storage_key, len(content)
