from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "correspondence",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
