import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend_common.media_path import safe_media_path
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
    target = safe_media_path(settings.media_path, f"desktop_backgrounds/{user_id}/{filename}")
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)
