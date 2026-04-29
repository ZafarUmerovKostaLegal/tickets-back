import os
import uuid
from pathlib import Path
from application.ports import MediaStoragePort
from infrastructure.config import get_settings


class LocalMediaStorage(MediaStoragePort):
    def __init__(self):
        self._base_path = Path(get_settings().media_path)

    def _ensure_dir(self) -> None:
        self._base_path.mkdir(parents=True, exist_ok=True)

    async def save(self, filename: str, content: bytes) -> str:
        self._ensure_dir()
        ext = Path(filename).suffix or ""
        unique_name = f"{uuid.uuid4().hex}{ext}"
        path = self._base_path / unique_name
        path.write_bytes(content)
        return unique_name

    async def get_path(self, filename: str) -> str:
        path = self._base_path / filename
        if not path.exists():
            raise FileNotFoundError(filename)
        return str(path)
