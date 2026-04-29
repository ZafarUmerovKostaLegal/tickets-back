from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from application.use_cases import (
    CreateTicketUseCase,
    GetTicketUseCase,
    ListTicketsUseCase,
    UpdateTicketUseCase,
    ArchiveTicketUseCase,
    CreateCommentUseCase,
    ListCommentsUseCase,
    UpdateCommentUseCase,
)
from application.ports import TicketRepositoryPort, CommentRepositoryPort, TicketFilters
from domain.entities import Status, Priority
from infrastructure.database import get_session
from infrastructure.repositories import TicketRepository, CommentRepository
from infrastructure.file_storage import save_attachment
from presentation.schemas import (
    TicketResponse,
    TicketUpdateRequest,
    StatusItem,
    PriorityItem,
    CommentResponse,
    CommentCreateRequest,
    CommentUpdateRequest,
)
from presentation.ws_hub import notify_ticket_event

router = APIRouter(prefix="/tickets", tags=["tickets"])


def get_ticket_repo(session: AsyncSession = Depends(get_session)) -> TicketRepositoryPort:
    return TicketRepository(session)


def get_comment_repo(session: AsyncSession = Depends(get_session)) -> CommentRepositoryPort:
    return CommentRepository(session)


def _ticket_to_response(t):
    return TicketResponse(
        id=t.id,
        uuid=t.uuid,
        theme=t.theme,
        description=t.description,
        attachment_path=t.attachment_path,
        status=t.status,
        created_by_user_id=t.created_by_user_id,
        created_at=t.created_at,
        category=t.category,
        priority=t.priority,
        is_archived=getattr(t, "is_archived", False),
    )


@router.get("/statuses")
async def list_statuses():
    return [StatusItem(value=s.value, label=s.value) for s in Status]


@router.get("/priorities")
async def list_priorities():
    return [PriorityItem(value=p.value, label=p.value) for p in Priority]


@router.post("", response_model=TicketResponse)
async def create_ticket(
    theme: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    priority: str = Form(...),
    created_by_user_id: int = Form(...),
    attachment: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_session),
    ticket_repo: TicketRepositoryPort = Depends(get_ticket_repo),
):
    valid_statuses = [s.value for s in Status]
    valid_priorities = [p.value for p in Priority]
    if priority not in valid_priorities:
        raise HTTPException(status_code=400, detail=f"Priority must be one of: {valid_priorities}")
    attachment_path = None
    if attachment and attachment.filename:
        try:
            content = await attachment.read()
            attachment_path = save_attachment(attachment.filename, content)
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e))
    uc = CreateTicketUseCase(ticket_repo)
    ticket = await uc.execute(
        theme=theme,
        description=description,
        attachment_path=attachment_path,
        created_by_user_id=created_by_user_id,
        category=category,
        priority=priority,
    )
    await session.commit()
    await notify_ticket_event("ticket_created", ticket_uuid=ticket.uuid)
    return _ticket_to_response(ticket)


@router.get("", response_model=list[TicketResponse])
async def list_tickets(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    created_by_user_id: Optional[int] = Query(None),
    include_archived: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    ticket_repo: TicketRepositoryPort = Depends(get_ticket_repo),
):
    filters = TicketFilters(
        skip=skip,
        limit=limit,
        status=status,
        priority=priority,
        category=category,
        created_by_user_id=created_by_user_id,
        include_archived=include_archived,
    )
    uc = ListTicketsUseCase(ticket_repo)
    tickets = await uc.execute(filters)
    return [_ticket_to_response(t) for t in tickets]


@router.patch("/{ticket_uuid}", response_model=TicketResponse)
async def update_ticket(
    ticket_uuid: str,
    body: TicketUpdateRequest,
    session: AsyncSession = Depends(get_session),
    ticket_repo: TicketRepositoryPort = Depends(get_ticket_repo),
):
    if body.status is not None and body.status not in [s.value for s in Status]:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {[s.value for s in Status]}")
    if body.priority is not None and body.priority not in [p.value for p in Priority]:
        raise HTTPException(status_code=400, detail=f"Invalid priority. Must be one of: {[p.value for p in Priority]}")
    uc = UpdateTicketUseCase(ticket_repo)
    ticket = await uc.execute(
        ticket_uuid=ticket_uuid,
        theme=body.theme,
        description=body.description,
        attachment_path=body.attachment_path,
        status=body.status,
        category=body.category,
        priority=body.priority,
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await session.commit()
    await notify_ticket_event("ticket_updated", ticket_uuid=ticket_uuid)
    return _ticket_to_response(ticket)


@router.patch("/{ticket_uuid}/archive", response_model=TicketResponse)
async def archive_ticket(
    ticket_uuid: str,
    is_archived: bool = True,
    session: AsyncSession = Depends(get_session),
    ticket_repo: TicketRepositoryPort = Depends(get_ticket_repo),
):
    uc = ArchiveTicketUseCase(ticket_repo)
    ticket = await uc.execute(ticket_uuid=ticket_uuid, is_archived=is_archived)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await session.commit()
    await notify_ticket_event("ticket_archived", ticket_uuid=ticket_uuid)
    return _ticket_to_response(ticket)


@router.get("/{ticket_uuid}", response_model=TicketResponse)
async def get_ticket(
    ticket_uuid: str,
    ticket_repo: TicketRepositoryPort = Depends(get_ticket_repo),
):
    uc = GetTicketUseCase(ticket_repo)
    ticket = await uc.execute(ticket_uuid)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _ticket_to_response(ticket)


@router.get("/{ticket_uuid}/comments", response_model=list[CommentResponse])
async def list_comments(
    ticket_uuid: str,
    ticket_repo: TicketRepositoryPort = Depends(get_ticket_repo),
    comment_repo: CommentRepositoryPort = Depends(get_comment_repo),
):
    uc_ticket = GetTicketUseCase(ticket_repo)
    ticket = await uc_ticket.execute(ticket_uuid)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    uc = ListCommentsUseCase(comment_repo)
    comments = await uc.execute(ticket.id)
    return [
        CommentResponse(
            id=c.id,
            ticket_id=c.ticket_id,
            user_id=c.user_id,
            content=c.content,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in comments
    ]


@router.post("/{ticket_uuid}/comments", response_model=CommentResponse)
async def create_comment(
    ticket_uuid: str,
    body: CommentCreateRequest,
    user_id: int = Query(..., description="ID пользователя"),
    session: AsyncSession = Depends(get_session),
    ticket_repo: TicketRepositoryPort = Depends(get_ticket_repo),
    comment_repo: CommentRepositoryPort = Depends(get_comment_repo),
):
    uc_ticket = GetTicketUseCase(ticket_repo)
    ticket = await uc_ticket.execute(ticket_uuid)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    uc = CreateCommentUseCase(comment_repo)
    comment = await uc.execute(ticket_id=ticket.id, user_id=user_id, content=body.content)
    await session.commit()
    await notify_ticket_event(
        "comment_created", ticket_uuid=ticket_uuid, comment_id=comment.id
    )
    return CommentResponse(
        id=comment.id,
        ticket_id=comment.ticket_id,
        user_id=comment.user_id,
        content=comment.content,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@router.patch("/{ticket_uuid}/comments/{comment_id}", response_model=CommentResponse)
async def update_comment(
    ticket_uuid: str,
    comment_id: int,
    body: CommentUpdateRequest,
    session: AsyncSession = Depends(get_session),
    ticket_repo: TicketRepositoryPort = Depends(get_ticket_repo),
    comment_repo: CommentRepositoryPort = Depends(get_comment_repo),
):
    uc_ticket = GetTicketUseCase(ticket_repo)
    ticket = await uc_ticket.execute(ticket_uuid)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    uc = UpdateCommentUseCase(comment_repo)
    comment = await uc.execute(comment_id=comment_id, content=body.content)
    if not comment or comment.ticket_id != ticket.id:
        raise HTTPException(status_code=404, detail="Comment not found")
    await session.commit()
    await notify_ticket_event(
        "comment_updated", ticket_uuid=ticket_uuid, comment_id=comment.id
    )
    return CommentResponse(
        id=comment.id,
        ticket_id=comment.ticket_id,
        user_id=comment.user_id,
        content=comment.content,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )

