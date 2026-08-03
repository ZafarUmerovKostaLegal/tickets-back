
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application.partner_report_confirmation_service import (
    confirm_partner_report_confirmation,
    count_partner_pending_signatures_for_viewer,
    create_partner_confirmation_comment,
    delete_partner_report_confirmation,
    list_confirmed_partner_confirmations,
    list_partner_confirmation_comments,
    list_pending_partner_confirmations,
    revoke_partner_report_confirmation_signature,
    set_partner_confirmation_review_priority,
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


class PartnerConfirmationCommentCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(..., min_length=1, max_length=4000)


class PartnerConfirmationReviewPriorityBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    review_priority: str = Field(..., alias="reviewPriority")


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


@router.delete("/partner-confirmations/{request_id}")
async def partner_report_confirmation_delete(
    request_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
):
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    return await delete_partner_report_confirmation(session, viewer, rid)


@router.delete("/partner-confirmations/{request_id}/signatures/{partner_auth_user_id}")
async def partner_report_confirmation_revoke_signature(
    request_id: str,
    partner_auth_user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Откат одной подписи партнёра без удаления всего отчёта."""
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    return await revoke_partner_report_confirmation_signature(
        session,
        viewer,
        rid,
        partner_auth_user_id,
        authorization=authorization,
    )


@router.patch("/partner-confirmations/{request_id}")
async def partner_report_confirmation_set_priority(
    request_id: str,
    body: PartnerConfirmationReviewPriorityBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    return await set_partner_confirmation_review_priority(
        session,
        viewer,
        rid,
        body.review_priority,
        authorization=authorization,
    )


@router.get("/partner-confirmations/pending/badge")
async def partner_report_confirmation_pending_badge(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
    scope: Optional[str] = Query(None),
):
    return await count_partner_pending_signatures_for_viewer(
        session,
        viewer,
        authorization=authorization,
        scope=scope,
    )


@router.get("/partner-confirmations/pending")
async def partner_report_confirmation_pending(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
    scope: Optional[str] = Query(
        None,
        description=(
            "mine (default) — заявки, где зритель обязательный партнёр "
            "или уже подписал; all — все незавершённые (менеджер/админ/партнёр)"
        ),
    ),
    priority: Optional[str] = Query(
        None,
        description="Фильтр приоритета: red | yellow | green",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200, alias="pageSize"),
    include_entry_counts: bool = Query(True, alias="includeEntryCounts"),
):
    return await list_pending_partner_confirmations(
        session,
        viewer,
        authorization=authorization,
        scope=scope,
        priority=priority,
        page=page,
        page_size=page_size,
        include_entry_counts=include_entry_counts,
    )


@router.get("/partner-confirmations/confirmed")
async def partner_report_confirmation_confirmed(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
    date_from: Optional[str] = Query(None, alias="dateFrom"),
    date_to: Optional[str] = Query(None, alias="dateTo"),
    before: Optional[str] = Query(
        None,
        description="Архив: подтверждения с dateTo строго раньше этой даты (YYYY-MM-DD)",
    ),
):
    df: date | None = None
    dt: date | None = None
    bf: date | None = None
    if date_from:
        try:
            df = date.fromisoformat(str(date_from).strip()[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid dateFrom")
    if date_to:
        try:
            dt = date.fromisoformat(str(date_to).strip()[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid dateTo")
    if before:
        try:
            bf = date.fromisoformat(str(before).strip()[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid before")
    if df and dt and dt < df:
        raise HTTPException(status_code=400, detail="dateTo не может быть раньше dateFrom")
    return await list_confirmed_partner_confirmations(
        session,
        viewer,
        authorization=authorization,
        date_from=df,
        date_to=dt,
        before=bf,
    )


@router.get("/partner-confirmations/{request_id}/comments")
async def partner_report_confirmation_comments_list(
    request_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    return await list_partner_confirmation_comments(
        session, viewer, rid, authorization=authorization
    )


@router.post("/partner-confirmations/{request_id}/comments")
async def partner_report_confirmation_comments_create(
    request_id: str,
    body: PartnerConfirmationCommentCreateBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
):
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    return await create_partner_confirmation_comment(
        session,
        viewer,
        rid,
        text=body.text,
        authorization=authorization,
    )
