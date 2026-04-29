import uuid
from pathlib import Path

from infrastructure.config import get_settings


def get_expenses_upload_dir(expense_request_id: str) -> Path:
    path = Path(get_settings().media_path) / "expenses" / expense_request_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_attachment(expense_request_id: str, filename: str, content: bytes) -> tuple[str, str]:

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError(f"File size exceeds {get_settings().max_upload_mb}MB")
    upload_dir = get_expenses_upload_dir(expense_request_id)
    ext = Path(filename).suffix if filename else ""
    unique_name = f"{uuid.uuid4().hex}{ext}"
    path = upload_dir / unique_name
    path.write_bytes(content)
    rel = str(path.relative_to(Path(get_settings().media_path)))
    return rel, unique_name
