import uuid
from pathlib import Path

from backend_common.media_path import safe_media_path
from infrastructure.config import get_settings


def get_expenses_upload_dir(expense_request_id: str) -> Path:
    path = safe_media_path(get_settings().media_path, f"expenses/{expense_request_id}")
    if path is None:
        raise ValueError("Invalid media path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_attachment(expense_request_id: str, filename: str, content: bytes) -> tuple[str, str]:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError(f"File size exceeds {get_settings().max_upload_mb}MB")
    get_expenses_upload_dir(expense_request_id)
    ext = Path(filename).suffix if filename else ""
    unique_name = f"{uuid.uuid4().hex}{ext}"
    rel = f"expenses/{expense_request_id}/{unique_name}"
    path = safe_media_path(get_settings().media_path, rel)
    if path is None:
        raise ValueError("Invalid media path")
    path.write_bytes(content)
    return rel, unique_name
