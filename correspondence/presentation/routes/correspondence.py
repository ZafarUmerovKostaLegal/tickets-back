import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from application.correspondence_service import (
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
from infrastructure.models import CorrespondenceAttachmentModel, CorrespondenceDocumentModel
from infrastructure.repositories import CorrespondenceRepository
from presentation.deps import check_manage_role, check_view_role, get_current_user
from presentation.schemas import (
    AttachmentOut,
    DocumentDetailOut,
    DocumentListItemOut,
    DocumentListResponse,
    DocumentPatchBody,
    StatsOut,
    UserSnippetOut,
)

router = APIRouter(prefix="/correspondence", tags=["correspondence"])

_INLINE_MIME_PREFIXES = ("image/", "application/pdf")


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


async def _validate_partner(partner_user_id: int, authorization: Optional[str]) -> None:
    settings = get_settings()
    profile = await fetch_user_by_id(settings.auth_service_url, authorization, partner_user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Партнёр не найден")
    if not is_partner_org_role(profile.get("role")):
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
    repo = CorrespondenceRepository(session)
    rows, total = await repo.list_documents(
        direction=(direction.strip() if direction else None),
        statuses=statuses,
        doc_types=doc_types,
        search=q,
        include_archived=include_archived,
        skip=skip,
        limit=limit,
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
    s = await repo.get_stats()
    return StatsOut(
        incoming_total=s["incoming_total"],
        outgoing_total=s["outgoing_total"],
        approval_total=s["approval_total"],
        incoming_new_total=s["incoming_new_total"],
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
    row = await repo.create_document(
        id_=doc_id,
        registry_number=reg_no,
        direction="incoming",
        doc_type=dt,
        status="new",
        counterparty=counterparty,
        subject=subject,
        comment=comment,
        partner_user_id=partner_user_id,
        responsible_user_id=uid,
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
    row = await repo.create_document(
        id_=doc_id,
        registry_number=reg_no,
        direction="outgoing",
        doc_type=dt,
        status="new",
        counterparty=counterparty,
        subject=subject,
        comment=comment,
        partner_user_id=None,
        responsible_user_id=uid,
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
    if row.archived_at is not None:
        raise HTTPException(status_code=400, detail="Архивный документ нельзя редактировать")
    new_status = None
    if "status" in data:
        try:
            new_status = normalize_status(data["status"])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    new_resp = data.get("responsible_user_id")
    if new_resp is not None and int(new_resp) <= 0:
        raise HTTPException(status_code=422, detail="responsibleUserId must be positive")
    await repo.update_document(
        row,
        status=new_status,
        responsible_user_id=int(new_resp) if new_resp is not None else None,
        comment=data.get("comment") if "comment" in data else None,
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
        raise HTTPException(status_code=404, detail="Дocument not found")
    if row.archived_at is not None:
        raise HTTPException(status_code=400, detail="Архивный документ нельзя изменять")
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
    if row.archived_at is not None:
        raise HTTPException(status_code=400, detail="Архивный документ нельзя изменять")
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
