from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/live")
async def live():
    return {"status": "ok", "service": "backup"}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "backup"}
