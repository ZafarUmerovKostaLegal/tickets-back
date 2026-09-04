import logging
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from application.correspondence_service import (
    REVIEW_EDITABLE_STATUSES,
    is_partner_org_role,
    normalize_attachment_kind,
    normalize_doc_type,
    normalize_status,
    parse_doc_type_filter,
    parse_status_filter,
    validate_upload_content,
)
from infrastructure.auth_users import fetch_user_by_id, fetch_users_by_ids
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.file_storage import resolve_storage_path, save_correspondence_file
from infrastructure.office_to_pdf import convert_office_bytes_to_pdf, is_office_document
from infrastructure.models import (
    CorrespondenceAttachmentModel,
    CorrespondenceDocumentCommentModel,
    CorrespondenceDocumentModel,
)
from infrastructure.notify import send_system_notification
from infrastructure.repositories import CorrespondenceRepository
from presentation.deps import check_manage_role, check_view_role, get_current_user
from presentation.schemas import (
    AttachmentOut,
    CommentListResponse,
    CommentOut,
    CreateCommentBody,
    DocumentDetailOut,
    DocumentListItemOut,
    DocumentListResponse,
    DocumentPatchBody,
    RejectReviewBody,
    StatsOut,
    SubmitReviewBody,
    UserSnippetOut,
)

router = APIRouter(prefix="/correspondence", tags=["correspondence"])
_log = logging.getLogger(__name__)

_INLINE_MIME_PREFIXES = ("image/", "application/pdf")


def _inline_response(path, media_type: str) -> FileResponse:
    """Avoid non-ASCII filenames in Content-Disposition (latin-1 headers → 500)."""
    return FileResponse(
        path,
        media_type=media_type,
        filename="preview",
        content_disposition_type="inline",
    )


def _user_snippet(user_id: int, profile: dict | None) -> UserSnippetOut:
    p = profile or {}
    return UserSnippetOut(
        id=user_id,
        display_name=p.get("display_name"),
        email=p.get("email"),
        picture=p.get("picture"),
        position=p.get("position"),
    )


def _attachment_out(row: CorrespondenceAttachmentModel) -> AttachmentOut:
    return AttachmentOut(
        id=row.id,
        file_name=row.file_name,
        content_type=row.content_type,
        size_bytes=int(row.size_bytes or 0),
        attachment_kind=row.attachment_kind,
        created_at=row.created_at,
    )


def _list_item(
    row: CorrespondenceDocumentModel,
    *,
    responsible: dict | None = None,
    partner: dict | None = None,
) -> DocumentListItemOut:
    atts = row.attachments or []
    has_scan = any(a.attachment_kind == "scan" for a in atts)
    return DocumentListItemOut(
        id=row.id,
        registry_number=row.registry_number,
        direction=row.direction,
        counterparty=row.counterparty,
        subject=row.subject,
        doc_type=row.doc_type,
        status=row.status,
        registered_at=row.registered_at,
        responsible_user_id=row.responsible_user_id,
        responsible_user=_user_snippet(row.responsible_user_id, responsible),
        partner_user_id=row.partner_user_id,
        partner_user=_user_snippet(row.partner_user_id, partner) if row.partner_user_id else None,
        attachments_count=len(atts),
        has_scan=has_scan,
        comment=row.comment,
        rejection_comment=row.rejection_comment,
        created_at=row.created_at,
    )


async def _detail(
    row: CorrespondenceDocumentModel,
    authorization: Optional[str],
) -> DocumentDetailOut:
    settings = get_settings()
    ids: set[int] = {row.responsible_user_id}
    if row.partner_user_id is not None:
        ids.add(row.partner_user_id)
    profiles = await fetch_users_by_ids(settings.auth_service_url, authorization, ids)
    li = _list_item(
        row,
        responsible=profiles.get(row.responsible_user_id),
        partner=profiles.get(row.partner_user_id) if row.partner_user_id else None,
    )
    atts = sorted(row.attachments or [], key=lambda a: a.created_at)
    return DocumentDetailOut(
        **li.model_dump(),
        attachments=[_attachment_out(a) for a in atts],
    )


def _comment_out(row: CorrespondenceDocumentCommentModel, profile: dict | None) -> CommentOut:
    return CommentOut(
        id=row.id,
        body=row.body,
        author_user_id=row.author_user_id,
        author_user=_user_snippet(row.author_user_id, profile),
        created_at=row.created_at,
    )


async def _validate_partner(partner_user_id: int, authorization: Optional[str]) -> None:
    settings = get_settings()
    profile = await fetch_user_by_id(settings.auth_service_url, authorization, partner_user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Партнёр не найден")
    if not is_partner_org_role(profile.get("role"), profile.get("position")):
        raise HTTPException(status_code=400, detail="Выбранный пользователь не является партнёром")


async def _save_uploads(
    repo: CorrespondenceRepository,
    *,
    document_id: str,
    files: list[UploadFile],
    attachment_kind: str,
    uploaded_by_user_id: int,
) -> None:
    for f in files:
        content = await f.read()
        if not content:
            continue
        try:
            mime = validate_upload_content(content, f.content_type)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        att_id = str(uuid.uuid4())
        try:
            storage_key = save_correspondence_file(document_id, att_id, f.filename or "file", content)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        await repo.add_attachment(
            attachment_id=att_id,
            document_id=document_id,
            file_name=f.filename or "file",
            content_type=mime,
            size_bytes=len(content),
            storage_key=storage_key,
            attachment_kind=attachment_kind,
            uploaded_by_user_id=uploaded_by_user_id,
        )
        await _maybe_store_pdf_preview(
            repo,
            document_id=document_id,
            original_name=f.filename or "file",
            original_mime=mime,
            content=content,
            attachment_kind=attachment_kind,
            uploaded_by_user_id=uploaded_by_user_id,
        )


async def _maybe_store_pdf_preview(
    repo: CorrespondenceRepository,
    *,
    document_id: str,
    original_name: str,
    original_mime: str,
    content: bytes,
    attachment_kind: str,
    uploaded_by_user_id: int,
) -> None:
    if not is_office_document(original_name, original_mime):
        return
    try:
        pdf_bytes = convert_office_bytes_to_pdf(content, original_name)
    except Exception:
        _log.exception("PDF preview was not stored for %s", original_name)
        return
    pdf_name = f"{Path(original_name).stem or 'letter'}.pdf"
    pdf_id = str(uuid.uuid4())
    try:
        pdf_key = save_correspondence_file(document_id, pdf_id, pdf_name, pdf_bytes)
    except ValueError as e:
        _log.warning("PDF preview save failed: %s", e)
        return
    await repo.add_attachment(
        attachment_id=pdf_id,
        document_id=document_id,
        file_name=pdf_name,
        content_type="application/pdf",
        size_bytes=len(pdf_bytes),
        storage_key=pdf_key,
        attachment_kind=attachment_kind,
        uploaded_by_user_id=uploaded_by_user_id,
    )


def _assert_not_archived(row: CorrespondenceDocumentModel) -> None:
    if row.archived_at is not None:
        raise HTTPException(status_code=400, detail="Архивный документ нельзя изменять")


def _assert_outgoing(row: CorrespondenceDocumentModel) -> None:
    if row.direction != "outgoing":
        raise HTTPException(status_code=400, detail="Действие доступно только для исходящих документов")


def _assert_author_or_manage(row: CorrespondenceDocumentModel, user: dict) -> None:
    uid = int(user["id"])
    if row.responsible_user_id == uid:
        return
    try:
        check_manage_role(user)
    except HTTPException:
        raise HTTPException(status_code=403, detail="Только автор или делопроизводитель могут изменить документ")


def _assert_assigned_partner(row: CorrespondenceDocumentModel, user: dict) -> None:
    uid = int(user["id"])
    if row.partner_user_id == uid and is_partner_org_role(user.get("role"), user.get("position")):
        return
    raise HTTPException(status_code=403, detail="Только назначенный партнёр может выполнить это действие")


def _parse_query_date(value: Optional[str], field: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be YYYY-MM-DD")


@router.get("", response_model=DocumentListResponse)
async def list_correspondence(
    direction: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    status_group: Optional[str] = Query(None, alias="statusGroup"),
    doc_type: Optional[str] = Query(None, alias="docType"),
    q: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(8, ge=1, le=200),
    include_archived: bool = Query(False, alias="includeArchived"),
    registered_only: bool = Query(False, alias="registeredOnly"),
    partner_user_id: Optional[int] = Query(None, alias="partnerUserId"),
    responsible_user_id: Optional[int] = Query(None, alias="responsibleUserId"),
    date_from: Optional[str] = Query(None, alias="dateFrom"),
    date_to: Optional[str] = Query(None, alias="dateTo"),
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    if direction and direction.strip() not in ("incoming", "outgoing"):
        raise HTTPException(status_code=400, detail="direction must be incoming or outgoing")
    try:
        statuses = parse_status_filter(status, status_group)
        doc_types = parse_doc_type_filter(doc_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    parsed_from = _parse_query_date(date_from, "dateFrom")
    parsed_to = _parse_query_date(date_to, "dateTo")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="dateFrom cannot be after dateTo")
    repo = CorrespondenceRepository(session)
    rows, total = await repo.list_documents(
        direction=(direction.strip() if direction else None),
        statuses=statuses,
        doc_types=doc_types,
        search=q,
        include_archived=include_archived,
        skip=skip,
        limit=limit,
        registered_only=registered_only,
        partner_user_id=partner_user_id,
        responsible_user_id=responsible_user_id,
        date_from=parsed_from,
        date_to=parsed_to,
    )
    settings = get_settings()
    ids: set[int] = set()
    for r in rows:
        ids.add(r.responsible_user_id)
        if r.partner_user_id is not None:
            ids.add(r.partner_user_id)
    profiles = await fetch_users_by_ids(settings.auth_service_url, authorization, ids)
    items = [
        _list_item(
            r,
            responsible=profiles.get(r.responsible_user_id),
            partner=profiles.get(r.partner_user_id) if r.partner_user_id else None,
        )
        for r in rows
    ]
    return DocumentListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/stats", response_model=StatsOut)
async def correspondence_stats(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    partner_uid: int | None = None
    if is_partner_org_role(user.get("role"), user.get("position")):
        try:
            partner_uid = int(user["id"])
        except (KeyError, TypeError, ValueError):
            partner_uid = None
    s = await repo.get_stats(partner_user_id=partner_uid)
    return StatsOut(
        incoming_total=s["incoming_total"],
        outgoing_total=s["outgoing_total"],
        approval_total=s["approval_total"],
        incoming_new_total=s["incoming_new_total"],
        pending_review_total=s.get("pending_review_total", 0),
        partner_attention_total=s.get("partner_attention_total", 0),
        partner_outgoing_pending=s.get("partner_outgoing_pending", 0),
        partner_incoming_new=s.get("partner_incoming_new", 0),
    )


@router.get("/{document_id}", response_model=DocumentDetailOut)
async def get_correspondence(
    document_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return await _detail(row, authorization)


@router.post("/incoming", response_model=DocumentDetailOut, status_code=201)
async def register_incoming(
    partner_user_id: int = Form(..., alias="partnerUserId"),
    counterparty: str = Form(...),
    subject: str = Form(...),
    doc_type: str = Form("letter", alias="docType"),
    comment: Optional[str] = Form(None),
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    if not (counterparty or "").strip():
        raise HTTPException(status_code=422, detail="counterparty is required")
    if not (subject or "").strip():
        raise HTTPException(status_code=422, detail="subject is required")
    if partner_user_id <= 0:
        raise HTTPException(status_code=422, detail="partnerUserId is required")
    if not files:
        raise HTTPException(status_code=422, detail="Для входящего документа приложите хотя бы один скан")
    has_content = False
    for f in files:
        chunk = await f.read()
        await f.seek(0)
        if chunk:
            has_content = True
            break
    if not has_content:
        raise HTTPException(status_code=422, detail="Для входящего документа приложите хотя бы один скан")
    try:
        dt = normalize_doc_type(doc_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await _validate_partner(partner_user_id, authorization)
    repo = CorrespondenceRepository(session)
    doc_id = str(uuid.uuid4())
    year = datetime.now(timezone.utc).year
    reg_no = await repo.next_registry_number("incoming", year)
    uid = int(user["id"])
    now = datetime.now(timezone.utc)
    row = await repo.create_document(
        id_=doc_id,
        registry_number=reg_no,
        direction="incoming",
        doc_type=dt,
        status="progress",
        counterparty=counterparty,
        subject=subject,
        comment=comment,
        partner_user_id=partner_user_id,
        responsible_user_id=uid,
        registered_at=now,
    )
    await _save_uploads(
        repo,
        document_id=doc_id,
        files=files,
        attachment_kind="scan",
        uploaded_by_user_id=uid,
    )
    await session.commit()
    row = await repo.get_by_id(doc_id, load_attachments=True)
    assert row is not None
    return await _detail(row, authorization)


@router.post("/outgoing", response_model=DocumentDetailOut, status_code=201)
async def register_outgoing(
    counterparty: str = Form(...),
    subject: str = Form(...),
    doc_type: str = Form("letter", alias="docType"),
    comment: Optional[str] = Form(None),
    files: list[UploadFile] | None = File(None),
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    """Immediate registry registration (contracts/notes bypass, office manager)."""
    check_view_role(user)
    if not (counterparty or "").strip():
        raise HTTPException(status_code=422, detail="counterparty is required")
    if not (subject or "").strip():
        raise HTTPException(status_code=422, detail="subject is required")
    try:
        dt = normalize_doc_type(doc_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    repo = CorrespondenceRepository(session)
    doc_id = str(uuid.uuid4())
    year = datetime.now(timezone.utc).year
    reg_no = await repo.next_registry_number("outgoing", year)
    uid = int(user["id"])
    now = datetime.now(timezone.utc)
    row = await repo.create_document(
        id_=doc_id,
        registry_number=reg_no,
        direction="outgoing",
        doc_type=dt,
        status="progress",
        counterparty=counterparty,
        subject=subject,
        comment=comment,
        partner_user_id=None,
        responsible_user_id=uid,
        registered_at=now,
    )
    if files:
        await _save_uploads(
            repo,
            document_id=doc_id,
            files=files,
            attachment_kind="attachment",
            uploaded_by_user_id=uid,
        )
    await session.commit()
    row = await repo.get_by_id(doc_id, load_attachments=True)
    return await _detail(row, authorization)


@router.post("/outgoing/draft", response_model=DocumentDetailOut, status_code=201)
async def create_outgoing_draft(
    counterparty: str = Form(...),
    subject: str = Form(...),
    doc_type: str = Form("letter", alias="docType"),
    comment: Optional[str] = Form(None),
    partner_user_id: Optional[int] = Form(None, alias="partnerUserId"),
    files: list[UploadFile] | None = File(None),
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    """Create outgoing letter draft without registry number (awaiting partner review)."""
    check_view_role(user)
    if not (counterparty or "").strip():
        raise HTTPException(status_code=422, detail="counterparty is required")
    if not (subject or "").strip():
        raise HTTPException(status_code=422, detail="subject is required")
    try:
        dt = normalize_doc_type(doc_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    partner_id = None
    if partner_user_id is not None and int(partner_user_id) > 0:
        await _validate_partner(int(partner_user_id), authorization)
        partner_id = int(partner_user_id)
    repo = CorrespondenceRepository(session)
    doc_id = str(uuid.uuid4())
    uid = int(user["id"])
    await repo.create_document(
        id_=doc_id,
        registry_number=None,
        direction="outgoing",
        doc_type=dt,
        status="draft",
        counterparty=counterparty,
        subject=subject,
        comment=comment,
        partner_user_id=partner_id,
        responsible_user_id=uid,
        registered_at=None,
    )
    if files:
        await _save_uploads(
            repo,
            document_id=doc_id,
            files=files,
            attachment_kind="attachment",
            uploaded_by_user_id=uid,
        )
    await session.commit()
    row = await repo.get_by_id(doc_id, load_attachments=True)
    return await _detail(row, authorization)


@router.post("/{document_id}/submit-review", response_model=DocumentDetailOut)
async def submit_outgoing_for_review(
    document_id: str,
    body: SubmitReviewBody,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _assert_not_archived(row)
    _assert_outgoing(row)
    _assert_author_or_manage(row, user)
    if row.status not in REVIEW_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="На проверку можно отправить только черновик или отклонённый документ",
        )
    if body.partner_user_id <= 0:
        raise HTTPException(status_code=422, detail="partnerUserId is required")
    await _validate_partner(body.partner_user_id, authorization)
    await repo.update_document(
        row,
        status="pending_review",
        partner_user_id=body.partner_user_id,
        clear_rejection_comment=True,
    )
    await session.commit()
    row = await repo.get_by_id(document_id, load_attachments=True)
    assert row is not None
    settings = get_settings()
    await send_system_notification(
        settings,
        recipient_user_id=body.partner_user_id,
        title="Исходящее письмо на проверке",
        description=(
            f"«{row.subject}» — {row.counterparty}. "
            "Откройте раздел корреспонденции, чтобы подтвердить или отклонить."
        ),
        notification_type="correspondence_review",
    )
    return await _detail(row, authorization)


@router.post("/{document_id}/approve", response_model=DocumentDetailOut)
async def approve_outgoing(
    document_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _assert_not_archived(row)
    _assert_outgoing(row)
    if row.status != "pending_review":
        raise HTTPException(status_code=400, detail="Документ не ожидает проверки")
    _assert_assigned_partner(row, user)
    year = datetime.now(timezone.utc).year
    now = datetime.now(timezone.utc)
    reg_no = await repo.next_registry_number("outgoing", year)
    await repo.update_document(
        row,
        status="progress",
        registry_number=reg_no,
        set_registered_at=True,
        registered_at=now,
        clear_rejection_comment=True,
    )
    await session.commit()
    row = await repo.get_by_id(document_id, load_attachments=True)
    assert row is not None
    settings = get_settings()
    await send_system_notification(
        settings,
        recipient_user_id=row.responsible_user_id,
        title="Исходящее письмо зарегистрировано",
        description=(
            f"Письмо «{row.subject}» зарегистрировано как {reg_no}."
        ),
        notification_type="correspondence_registered",
    )
    return await _detail(row, authorization)


@router.post("/{document_id}/reject", response_model=DocumentDetailOut)
async def reject_outgoing(
    document_id: str,
    body: RejectReviewBody,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    comment = (body.comment or "").strip()
    if not comment:
        raise HTTPException(status_code=422, detail="Укажите комментарий при отказе")
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _assert_not_archived(row)
    _assert_outgoing(row)
    if row.status != "pending_review":
        raise HTTPException(status_code=400, detail="Документ не ожидает проверки")
    _assert_assigned_partner(row, user)
    await repo.update_document(
        row,
        status="rejected",
        rejection_comment=comment,
    )
    await session.commit()
    row = await repo.get_by_id(document_id, load_attachments=True)
    assert row is not None
    settings = get_settings()
    await send_system_notification(
        settings,
        recipient_user_id=row.responsible_user_id,
        title="Исходящее письмо отклонено",
        description=(
            f"Письмо «{row.subject}» отклонено партнёром. Комментарий: {comment}"
        ),
        notification_type="correspondence_rejected",
    )
    return await _detail(row, authorization)


@router.patch("/{document_id}", response_model=DocumentDetailOut)
async def patch_correspondence(
    document_id: str,
    body: DocumentPatchBody,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    data = body.model_dump(exclude_unset=True)
    needs_manage = any(k in data for k in ("status", "responsible_user_id"))
    if needs_manage:
        check_manage_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _assert_not_archived(row)

    content_keys = {"counterparty", "subject", "comment", "partner_user_id"}
    if any(k in data for k in content_keys):
        if row.direction == "outgoing" and row.status in REVIEW_EDITABLE_STATUSES:
            _assert_author_or_manage(row, user)
        elif needs_manage is False and "comment" in data and len(data) == 1:
            pass
        else:
            check_manage_role(user)

    new_status = None
    if "status" in data:
        try:
            new_status = normalize_status(data["status"])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if new_status in ("draft", "pending_review", "rejected"):
            raise HTTPException(
                status_code=422,
                detail="Статусы проверки меняются через submit-review / approve / reject",
            )
    new_resp = data.get("responsible_user_id")
    if new_resp is not None and int(new_resp) <= 0:
        raise HTTPException(status_code=422, detail="responsibleUserId must be positive")
    partner_id = data.get("partner_user_id")
    if partner_id is not None:
        if int(partner_id) <= 0:
            raise HTTPException(status_code=422, detail="partnerUserId must be positive")
        await _validate_partner(int(partner_id), authorization)
    await repo.update_document(
        row,
        status=new_status,
        responsible_user_id=int(new_resp) if new_resp is not None else None,
        comment=data.get("comment") if "comment" in data else None,
        counterparty=data.get("counterparty") if "counterparty" in data else None,
        subject=data.get("subject") if "subject" in data else None,
        partner_user_id=int(partner_id) if partner_id is not None else None,
    )
    await session.commit()
    row = await repo.get_by_id(document_id, load_attachments=True)
    return await _detail(row, authorization)


@router.post("/{document_id}/archive", response_model=DocumentDetailOut)
async def archive_correspondence(
    document_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    check_manage_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if row.archived_at is not None:
        return await _detail(row, authorization)
    await repo.archive_document(row)
    await session.commit()
    row = await repo.get_by_id(document_id, load_attachments=True)
    return await _detail(row, authorization)


@router.get("/{document_id}/comments", response_model=CommentListResponse)
async def list_document_comments(
    document_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    comments = await repo.list_comments(document_id)
    settings = get_settings()
    author_ids = {c.author_user_id for c in comments}
    profiles = await fetch_users_by_ids(settings.auth_service_url, authorization, author_ids) if author_ids else {}
    return CommentListResponse(
        items=[_comment_out(c, profiles.get(c.author_user_id)) for c in comments],
    )


@router.post("/{document_id}/comments", response_model=CommentOut, status_code=201)
async def create_document_comment(
    document_id: str,
    body: CreateCommentBody,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Текст комментария обязателен")
    if len(text) > 4000:
        raise HTTPException(status_code=422, detail="Комментарий слишком длинный")
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _assert_not_archived(row)
    author_id = int(user["id"])
    comment = await repo.add_comment(
        comment_id=str(uuid.uuid4()),
        document_id=document_id,
        author_user_id=author_id,
        body=text,
    )
    await session.commit()

    notify_ids: set[int] = set()
    if row.responsible_user_id and row.responsible_user_id != author_id:
        notify_ids.add(row.responsible_user_id)
    if row.partner_user_id and row.partner_user_id != author_id:
        notify_ids.add(row.partner_user_id)
    settings = get_settings()
    preview = text if len(text) <= 160 else f"{text[:157]}…"
    label = row.registry_number or row.subject or "документ"
    for recipient_id in notify_ids:
        await send_system_notification(
            settings,
            recipient_user_id=recipient_id,
            title="Новый комментарий",
            description=f"«{label}»: {preview}",
            notification_type="correspondence_comment",
        )

    profiles = await fetch_users_by_ids(settings.auth_service_url, authorization, {author_id})
    return _comment_out(comment, profiles.get(author_id))


@router.post("/{document_id}/attachments", response_model=DocumentDetailOut)
async def upload_attachment(
    document_id: str,
    file: UploadFile = File(...),
    attachment_kind: Optional[str] = Form(None, alias="attachmentKind"),
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _assert_not_archived(row)
    if row.direction == "outgoing" and row.status in REVIEW_EDITABLE_STATUSES:
        _assert_author_or_manage(row, user)
    default_kind = "scan" if row.direction == "incoming" else "attachment"
    try:
        kind = normalize_attachment_kind(attachment_kind, default=default_kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _save_uploads(
        repo,
        document_id=document_id,
        files=[file],
        attachment_kind=kind,
        uploaded_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(document_id, load_attachments=True)
    return await _detail(row, authorization)


@router.get("/{document_id}/attachments/{attachment_id}/file")
async def download_attachment_file(
    document_id: str,
    attachment_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    att = next((a for a in (row.attachments or []) if a.id == attachment_id), None)
    if not att:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    path = resolve_storage_path(att.storage_key)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден в хранилище")
    mime = (att.content_type or "application/octet-stream").strip()
    disp = "inline" if mime.startswith(_INLINE_MIME_PREFIXES) else "attachment"
    return FileResponse(
        path,
        media_type=mime,
        filename=att.file_name,
        content_disposition_type=disp,
    )


@router.get("/{document_id}/attachments/{attachment_id}/preview")
async def preview_attachment_file(
    document_id: str,
    attachment_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Inline PDF/image for the document card. Word files are converted to PDF."""
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    att = next((a for a in (row.attachments or []) if a.id == attachment_id), None)
    if not att:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    path = resolve_storage_path(att.storage_key)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден в хранилище")
    mime = (att.content_type or "application/octet-stream").strip().lower()
    name = (att.file_name or "").lower()
    if mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        return _inline_response(path, att.content_type or "image/jpeg")
    if mime == "application/pdf" or name.endswith(".pdf"):
        return _inline_response(path, "application/pdf")
    if not is_office_document(att.file_name, att.content_type):
        raise HTTPException(status_code=415, detail="Предпросмотр для этого типа файла недоступен")
    cache_path = path.with_suffix(path.suffix + ".preview.pdf")
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return _inline_response(cache_path, "application/pdf")
    try:
        pdf_bytes = convert_office_bytes_to_pdf(path.read_bytes(), att.file_name)
    except Exception as e:
        _log.exception("preview convert failed for attachment %s", att.id)
        raise HTTPException(
            status_code=503,
            detail="Не удалось преобразовать письмо в PDF. Скачайте исходный файл Word.",
        ) from e
    try:
        cache_path.write_bytes(pdf_bytes)
    except OSError:
        _log.warning("could not cache preview pdf for %s", att.id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=preview.pdf"},
    )


@router.delete("/{document_id}/attachments/{attachment_id}", response_model=DocumentDetailOut)
async def delete_attachment(
    document_id: str,
    attachment_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = CorrespondenceRepository(session)
    row = await repo.get_by_id(document_id, load_attachments=True)
    if not row:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _assert_not_archived(row)
    if row.direction == "outgoing" and row.status in REVIEW_EDITABLE_STATUSES:
        _assert_author_or_manage(row, user)
    att = next((a for a in (row.attachments or []) if a.id == attachment_id), None)
    if not att:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    if row.direction == "incoming" and att.attachment_kind == "scan":
        scan_count = await repo.count_attachments_by_kind(document_id, "scan")
        if scan_count <= 1:
            raise HTTPException(status_code=422, detail="Нельзя удалить последний скан входящего документа")
    await repo.delete_attachment(att)
    await session.commit()
    row = await repo.get_by_id(document_id, load_attachments=True)
    return await _detail(row, authorization)
