from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.integrity_audit import audit_tt_integrity
from application.retention_dry_run import _dry_run_enabled, audit_tt_retention_dry_run
from infrastructure.database import get_session
from presentation.deps import require_tt_reports_viewer

router = APIRouter(prefix="/integrity", tags=["integrity_audit"])


@router.get("/audit")
async def integrity_audit(
    session: AsyncSession = Depends(get_session),
    _viewer: dict = Depends(require_tt_reports_viewer),
):
    """Read-only counts of possible orphan references. Never mutates data."""
    return await audit_tt_integrity(session)


@router.get("/retention-dry-run")
async def retention_dry_run(
    session: AsyncSession = Depends(get_session),
    _viewer: dict = Depends(require_tt_reports_viewer),
):
    """COUNT archives/snapshots older than RETENTION_DAYS. Never deletes."""
    if not _dry_run_enabled():
        raise HTTPException(
            status_code=404,
            detail="Retention dry-run disabled (RETENTION_DRY_RUN=0)",
        )
    return await audit_tt_retention_dry_run(session)
