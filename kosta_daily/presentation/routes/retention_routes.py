from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.retention_dry_run import _dry_run_enabled, audit_chat_retention_dry_run
from infrastructure.database import get_session
from presentation.dependencies import get_current_user_id
from typing import Annotated

router = APIRouter(prefix="/retention", tags=["retention"])


@router.get("/dry-run")
async def retention_dry_run(
    _user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    """COUNT soft-deleted chat_messages older than RETENTION_DAYS. Never deletes."""
    if not _dry_run_enabled():
        raise HTTPException(
            status_code=404,
            detail="Retention dry-run disabled (RETENTION_DRY_RUN=0)",
        )
    return await audit_chat_retention_dry_run(session)
