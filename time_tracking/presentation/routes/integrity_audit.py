from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from application.integrity_audit import audit_tt_integrity
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
