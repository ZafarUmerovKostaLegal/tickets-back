from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings

router = APIRouter(prefix="/api/v1/media", tags=["media"])


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return await verify_bearer_and_get_user(request, authorization)


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

