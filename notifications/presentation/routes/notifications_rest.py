from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from infrastructure.database import get_session
from infrastructure.repositories import NotificationRepository
from application.ports import NotificationFilters
from application.use_cases import (
    CreateNotificationUseCase,
    GetNotificationUseCase,
    ListNotificationsUseCase,
    UpdateNotificationUseCase,
    ArchiveNotificationUseCase,
    DeleteNotificationUseCase,
)
from presentation.deps import require_bearer_user, require_partner_it_office_write
from presentation.schemas import (
    NotificationResponse,
    NotificationCreate,
    NotificationUpdate,
    NotificationArchive,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _to_response(n):
    return NotificationResponse(
        id=n.id,
        uuid=n.uuid,
        title=n.title,
        description=n.description,
        photo_path=n.photo_path,
        is_archived=n.is_archived,
        created_at=n.created_at,
        updated_at=n.updated_at,
    )


def get_repo(session: AsyncSession = Depends(get_session)) -> NotificationRepository:
    return NotificationRepository(session)


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    include_archived: bool = False,
    _: dict = Depends(require_bearer_user),
    repo: NotificationRepository = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    filters = NotificationFilters(skip=skip, limit=limit, include_archived=include_archived)
    uc = ListNotificationsUseCase(repo)
    items = await uc.execute(filters)
    return [_to_response(n) for n in items]


@router.get("/{notification_uuid}", response_model=NotificationResponse)
async def get_notification(
    notification_uuid: str,
    _: dict = Depends(require_bearer_user),
    repo: NotificationRepository = Depends(get_repo),
):
    uc = GetNotificationUseCase(repo)
    n = await uc.execute(notification_uuid)
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _to_response(n)


@router.post("", response_model=NotificationResponse, status_code=201)
async def create_notification(
    body: NotificationCreate,
    _: dict = Depends(require_partner_it_office_write),
    repo: NotificationRepository = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = CreateNotificationUseCase(repo)
    n = await uc.execute(
        title=body.title,
        description=body.description,
        photo_path=body.photo_path,
    )
    await session.commit()
    return _to_response(n)


@router.patch("/{notification_uuid}", response_model=NotificationResponse)
async def update_notification(
    notification_uuid: str,
    body: NotificationUpdate,
    _: dict = Depends(require_partner_it_office_write),
    repo: NotificationRepository = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):

    uc = UpdateNotificationUseCase(repo)
    n = await uc.execute(
        notification_uuid=notification_uuid,
        title=body.title,
        description=body.description,
        photo_path=body.photo_path,
    )
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    await session.commit()
    return _to_response(n)


@router.post("/{notification_uuid}/archive", response_model=NotificationResponse)
async def archive_notification(
    notification_uuid: str,
    body: NotificationArchive,
    _: dict = Depends(require_partner_it_office_write),
    repo: NotificationRepository = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = ArchiveNotificationUseCase(repo)
    n = await uc.execute(
        notification_uuid=notification_uuid,
        is_archived=body.is_archived,
    )
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    await session.commit()
    return _to_response(n)


@router.delete("/{notification_uuid}", status_code=204)
async def delete_notification(
    notification_uuid: str,
    _: dict = Depends(require_partner_it_office_write),
    repo: NotificationRepository = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = DeleteNotificationUseCase(repo)
    ok = await uc.execute(notification_uuid)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    await session.commit()
