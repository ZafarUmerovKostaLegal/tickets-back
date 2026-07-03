import re
import uuid
from pathlib import Path

from infrastructure.config import get_settings

MAX_SIZE_BYTES = get_settings().max_attachment_size_mb * 1024 * 1024

def get_tickets_upload_dir() -> Path:
    path = Path(get_settings().media_path) / "tickets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_attachment(filename: str, content: bytes) -> None:
    if len(content) > MAX_SIZE_BYTES:
        raise ValueError(f"File size exceeds {get_settings().max_attachment_size_mb}MB")
    if not (filename or "").strip() and not content:
        raise ValueError("Empty file")


def _safe_base(filename: str) -> str:
    base = Path(filename or "").name
    stem = Path(base).stem
    ext = Path(base).suffix
    stem = re.sub(r"[^\w.\-]+", "_", stem, flags=re.UNICODE).strip("._")[:80]
    ext = re.sub(r"[^\w.]+", "", ext)[:16]
    return f"{stem or 'file'}{ext}"


def save_attachment(filename: str, content: bytes) -> str:
    _validate_attachment(filename, content)
    upload_dir = get_tickets_upload_dir()
                                                                           
                                                                                  
    unique_name = f"{uuid.uuid4().hex}_{_safe_base(filename)}"
    path = upload_dir / unique_name
    path.write_bytes(content)
    return unique_name
