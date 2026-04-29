import uuid
from pathlib import Path

from infrastructure.config import get_settings

MAX_SIZE_BYTES = get_settings().max_photo_size_mb * 1024 * 1024

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def get_inventory_upload_dir() -> Path:
    path = Path(get_settings().media_path) / "inventory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_photo(filename: str, content: bytes) -> None:
    if len(content) > MAX_SIZE_BYTES:
        raise ValueError(f"File size exceeds {get_settings().max_photo_size_mb}MB")
    ext = (Path(filename).suffix or "").lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")


def save_photo(filename: str, content: bytes) -> str:
    _validate_photo(filename, content)
    upload_dir = get_inventory_upload_dir()
    ext = Path(filename).suffix if filename else ""
    unique_name = f"{uuid.uuid4().hex}{ext}"
    path = upload_dir / unique_name
    path.write_bytes(content)
    return str(path.relative_to(Path(get_settings().media_path)))
