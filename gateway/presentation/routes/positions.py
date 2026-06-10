from typing import Optional

from fastapi import APIRouter, Depends, Header, Request

from backend_common.positions import list_positions
from infrastructure.auth_upstream import verify_bearer_and_get_user

router = APIRouter(prefix="/api/v1", tags=["positions"])


async def _current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return await verify_bearer_and_get_user(request, authorization)


@router.get("/positions")
async def get_positions(_: dict = Depends(_current_user)) -> dict:
    """Справочник должностей для выпадающего списка на фронте.

    По этим должностям делятся доступы в time tracking.
    """
    return {"positions": list_positions()}
