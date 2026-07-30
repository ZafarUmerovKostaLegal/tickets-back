from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application.ports import UserRepositoryPort
from backend_common.ws_internal_auth import has_valid_internal_ws_key
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.repositories import UserRepository

router = APIRouter(prefix="/internal", tags=["internal"])


def get_user_repo(session: AsyncSession = Depends(get_session)) -> UserRepositoryPort:
    return UserRepository(session)


def _require_internal_key(x_internal_key: str | None = Header(None, alias="X-Internal-Key")) -> None:
    if not has_valid_internal_ws_key(get_settings().ws_internal_secret, x_internal_key):
        raise HTTPException(status_code=403, detail="Invalid internal key")


@router.get("/users/initials")
async def internal_users_initials(
    ids: str = Query(..., description="ID через запятую, max 500"),
    _: None = Depends(_require_internal_key),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):
    raw_ids: list[int] = []
    for chunk in (ids or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            raw_ids.append(int(chunk))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid user id: {chunk!r}") from exc
    requested = sorted({i for i in raw_ids if i > 0})
    if not requested:
        raise HTTPException(status_code=400, detail="Query parameter ids is required")
    if len(requested) > 500:
        raise HTTPException(status_code=400, detail="Too many ids (max 500)")
    rows = await user_repo.get_many_by_ids(requested)
    return {
        str(user.id): user.initials
        for user in rows
    }


def _parse_ids_query(ids: str, *, max_ids: int = 500) -> list[int]:
    raw_ids: list[int] = []
    for chunk in (ids or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            raw_ids.append(int(chunk))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid user id: {chunk!r}") from exc
    requested = sorted({i for i in raw_ids if i > 0})
    if not requested:
        raise HTTPException(status_code=400, detail="Query parameter ids is required")
    if len(requested) > max_ids:
        raise HTTPException(status_code=400, detail=f"Too many ids (max {max_ids})")
    return requested


@router.get("/users/by-ids")
async def internal_users_by_ids(
    ids: str = Query(..., description="ID через запятую, max 500"),
    _: None = Depends(_require_internal_key),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):
    """Batch public profile for TT dual-write collapse (auth = PII source of truth)."""
    requested = _parse_ids_query(ids)
    rows = await user_repo.get_many_by_ids(requested)
    return {
        str(user.id): {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "picture": user.picture,
            "position": user.position,
            "role": user.role,
            "initials": user.initials,
            "is_blocked": bool(user.is_blocked),
            "is_archived": bool(user.is_archived),
            "time_tracking_role": user.time_tracking_role,
        }
        for user in rows
    }


@router.get("/users/by-email")
async def internal_user_by_email(
    email: str = Query(..., min_length=3),
    _: None = Depends(_require_internal_key),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):
    """Resolve a user for service-to-service targeted notifications."""
    user = await user_repo.get_by_email(email)
    if user is None or bool(user.is_archived):
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "picture": user.picture,
        "position": user.position,
        "role": user.role,
        "is_blocked": bool(user.is_blocked),
        "is_archived": bool(user.is_archived),
    }
