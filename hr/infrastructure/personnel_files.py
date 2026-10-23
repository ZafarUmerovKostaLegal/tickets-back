from __future__ import annotations

from pathlib import Path

from backend_common.media_path import safe_media_path

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_PHOTO_BYTES = 8 * 1024 * 1024

DOCUMENT_SLOTS = frozenset({"contract", "id", "education", "medical", "other"})
PHOTO_SLOT = "photo"
ALLOWED_SLOTS = DOCUMENT_SLOTS | {PHOTO_SLOT}

ALLOWED_DOCUMENT_SUFFIXES = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".doc", ".docx", ".xls", ".xlsx", ".txt"}
)
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})


def normalize_slot(raw: str | None) -> str:
    slot = (raw or "").strip().lower()
    if slot not in ALLOWED_SLOTS:
        allowed = ", ".join(sorted(ALLOWED_SLOTS))
        raise ValueError(f"Раздел файла должен быть одним из: {allowed}")
    return slot


def file_suffix(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def is_image_file(filename: str, content_type: str | None = None) -> bool:
    if file_suffix(filename) in IMAGE_SUFFIXES:
        return True
    mime = (content_type or "").split(";")[0].strip().lower()
    return mime in IMAGE_TYPES


def validate_upload(*, filename: str, content: bytes, slot: str) -> None:
    name = (filename or "").strip()
    if not name:
        raise ValueError("У файла нет имени")
    if not content:
        raise ValueError(f"Файл «{name}» пустой")
    limit = MAX_PHOTO_BYTES if slot == PHOTO_SLOT else MAX_DOCUMENT_BYTES
    if len(content) > limit:
        raise ValueError(f"Файл «{name}» больше {limit // (1024 * 1024)} МБ")
    suffix = file_suffix(name)
    if slot == PHOTO_SLOT:
        if suffix not in IMAGE_SUFFIXES:
            raise ValueError("Главное фото: JPG, PNG, WEBP или GIF")
        return
    if suffix not in ALLOWED_DOCUMENT_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_DOCUMENT_SUFFIXES))
        raise ValueError(f"Недопустимый тип файла «{name}». Разрешены: {allowed}")


def personnel_storage_key(user_id: int, file_id: str, filename: str) -> str:
    if user_id <= 0:
        raise ValueError("Некорректный сотрудник")
    safe_id = "".join(ch for ch in file_id if ch.isalnum())
    if len(safe_id) < 8:
        raise ValueError("Некорректный идентификатор файла")
    suffix = file_suffix(filename)
    if suffix and not suffix.replace(".", "").isalnum():
        suffix = ""
    return f"hr_personnel/{user_id}/{safe_id}{suffix}"


def save_personnel_bytes(base: Path, storage_key: str, data: bytes) -> None:
    target = safe_media_path(base, storage_key)
    if target is None:
        raise ValueError("Некорректный путь файла")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def personnel_disk_path(base: Path, storage_key: str) -> Path | None:
    target = safe_media_path(base, storage_key)
    if target is None or not target.is_file():
        return None
    return target


def delete_personnel_bytes(base: Path, storage_key: str) -> None:
    target = safe_media_path(base, storage_key)
    if target is not None and target.is_file():
        target.unlink()
