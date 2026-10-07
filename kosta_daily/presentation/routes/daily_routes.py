from fastapi import APIRouter

from infrastructure.config import get_settings

router = APIRouter(tags=["kosta-daily"])


@router.get("")
async def root() -> dict:
    return {
        "service": get_settings().service_name,
        "status": "ok",
        "message": "Kosta Daily microservice",
    }
