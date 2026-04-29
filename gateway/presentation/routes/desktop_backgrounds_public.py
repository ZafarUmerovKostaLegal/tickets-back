

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from infrastructure.config import get_settings

router = APIRouter(tags=["desktop_backgrounds"])


_SAFE_FILENAME = re.compile(
    r"^[a-f0-9]{32}\.(jpg|jpeg|png|gif|webp)$",
    re.IGNORECASE,
)


@router.get("/desktop_backgrounds/{user_id}/{filename}")
async def serve_desktop_background(user_id: int, filename: str):
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=404, detail="Not found")
    settings = get_settings()
    base_dir = Path(settings.media_path).resolve()
    target = (base_dir / "desktop_backgrounds" / str(user_id) / filename).resolve()
    if not str(target).startswith(str(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)
