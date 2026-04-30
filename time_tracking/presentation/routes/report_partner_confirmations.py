
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application.partner_report_confirmation_service import (
    confirm_partner_report_confirmation,
    list_confirmed_partner_confirmations,
    list_pending_partner_confirmations,
    submit_partner_report_confirmation,
    submit_partner_report_confirmation_from_preview,
)
from infrastructure.database import get_session
from presentation.deps import require_bearer_user

router = APIRouter(prefix="/reports", tags=["reports"])


class PartnerReportConfirmationSubmitBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    snapshot_id: str = Field(..., alias="snapshotId")
    project_id: str = Field(..., alias="projectId")
    date_from: date = Field(..., alias="dateFrom")
    date_to: date = Field(..., alias="dateTo")


class PartnerReportConfirmationSubmitFromPreviewBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    snapshot_id: Optional[str] = Field(None, alias="snapshotId")
    project_id: str = Field(..., alias="projectId")
    date_from: date = Field(..., alias="dateFrom")
    date_to: date = Field(..., alias="dateTo")


@router.post("/partner-confirmations/submit-from-preview")
async def partner_report_confirmation_submit_from_preview(
    body: PartnerReportConfirmationSubmitFromPreviewBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    if body.date_to < body.date_from:
        raise HTTPException(status_code=400, detail="dateTo не может быть раньше dateFrom")
    sid = (body.snapshot_id or "").strip()
    if sid:
        return await submit_partner_report_confirmation(
            session,
            viewer,
            snapshot_id=sid,
            project_id=body.project_id.strip(),
            date_from=body.date_from,
            date_to=body.date_to,
            authorization=authorization,
        )
    return await submit_partner_report_confirmation_from_preview(
        session,
        viewer,
        project_id=body.project_id.strip(),
        date_from=body.date_from,
        date_to=body.date_to,
        authorization=authorization,
    )


@router.post("/partner-confirmations/submit")
async def partner_report_confirmation_submit(
    body: PartnerReportConfirmationSubmitBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    if body.date_to < body.date_from:
        raise HTTPException(status_code=400, detail="dateTo не может быть раньше dateFrom")
    return await submit_partner_report_confirmation(
        session,
        viewer,
        snapshot_id=body.snapshot_id.strip(),
        project_id=body.project_id.strip(),
        date_from=body.date_from,
        date_to=body.date_to,
        authorization=authorization,
    )


@router.post("/partner-confirmations/{request_id}/confirm")
async def partner_report_confirmation_confirm(
    request_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    return await confirm_partner_report_confirmation(
        session, viewer, rid, authorization=authorization
    )


@router.get("/partner-confirmations/pending")
async def partner_report_confirmation_pending(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    return await list_pending_partner_confirmations(
        session, viewer, authorization=authorization
    )


@router.get("/partner-confirmations/confirmed")
async def partner_report_confirmation_confirmed(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    return await list_confirmed_partner_confirmations(
        session, viewer, authorization=authorization
    )
