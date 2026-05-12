import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings

router = APIRouter(prefix="/api/v1/media", tags=["media"])

_DESKTOP_BG_FILENAME = re.compile(
    r"^[a-f0-9]{32}\.(jpg|jpeg|png|gif|webp)$",
    re.IGNORECASE,
)


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return await verify_bearer_and_get_user(request, authorization)


@router.get("/desktop_backgrounds/{user_id}/{filename}")
async def get_desktop_background_media(user_id: int, filename: str):
    """Фон рабочего стола: без JWT в заголовке (удобно для <img src=\"...\">). Те же правила, что /desktop_backgrounds/..."""

    if not _DESKTOP_BG_FILENAME.match(filename):
        raise HTTPException(status_code=404, detail="Not found")
    settings = get_settings()
    base_dir = Path(settings.media_path).resolve()
    target = (base_dir / "desktop_backgrounds" / str(user_id) / filename).resolve()
    if not str(target).startswith(str(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid media path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(target)


@router.get("/{subpath:path}")
async def get_media(subpath: str, _: dict = Depends(get_current_user)):
    settings = get_settings()
    base_dir = Path(settings.media_path).resolve()
    target_path = (base_dir / subpath).resolve()

    if not str(target_path).startswith(str(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid media path")

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")

    return FileResponse(target_path)

