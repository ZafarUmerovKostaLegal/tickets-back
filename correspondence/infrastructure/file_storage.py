import uuid
from pathlib import Path

from backend_common.media_path import safe_media_path
from infrastructure.config import get_settings


def get_correspondence_upload_dir(document_id: str) -> Path:
    path = safe_media_path(get_settings().media_path, f"correspondence/{document_id}")
    if path is None:
        raise ValueError("Invalid media path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_correspondence_file(document_id: str, attachment_id: str, filename: str, content: bytes) -> str:
    max_bytes = get_settings().max_file_bytes
    if len(content) > max_bytes:
        raise ValueError(f"File size exceeds {max_bytes // (1024 * 1024)}MB")
    get_correspondence_upload_dir(document_id)
    ext = Path(filename).suffix if filename else ""
    unique_name = f"{attachment_id}_{uuid.uuid4().hex[:8]}{ext}"
    rel = f"correspondence/{document_id}/{unique_name}"
    path = safe_media_path(get_settings().media_path, rel)
    if path is None:
        raise ValueError("Invalid media path")
    path.write_bytes(content)
    return rel


def resolve_storage_path(storage_key: str) -> Path | None:
    return safe_media_path(get_settings().media_path, storage_key)


def delete_correspondence_storage(document_id: str, storage_keys: list[str]) -> None:
    """Remove attachment files after the document row is committed deleted."""
    for key in storage_keys:
        path = resolve_storage_path(key)
        if path is not None and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
    folder = safe_media_path(get_settings().media_path, f"correspondence/{document_id}")
    if folder is not None and folder.is_dir():
        try:
            folder.rmdir()
        except OSError:
            pass
