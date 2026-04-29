from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from application.use_cases import (
    CreateItemUseCase,
    GetItemUseCase,
    ListItemsUseCase,
    UpdateItemUseCase,
    AssignItemUseCase,
    UnassignItemUseCase,
    ArchiveItemUseCase,
    DeleteItemUseCase,
)
from application.ports import InventoryRepositoryPort, ItemFilters
from infrastructure.database import get_session
from infrastructure.repositories import InventoryRepository
from infrastructure.file_storage import save_photo
from presentation.schemas import (
    InventoryItemResponse,
    InventoryItemCreate,
    InventoryItemUpdate,
    AssignRequest,
    StatusItem,
)

router = APIRouter(prefix="/items", tags=["items"])

STATUSES = [
    ("in_use", "В использовании"),
    ("in_stock", "На складе"),
    ("repair", "В ремонте"),
    ("written_off", "Списано"),
]


def get_repo(session: AsyncSession = Depends(get_session)) -> InventoryRepositoryPort:
    return InventoryRepository(session)


def _to_response(i):
    return InventoryItemResponse(
        id=i.id,
        uuid=i.uuid,
        name=i.name,
        description=i.description,
        category_id=i.category_id,
        photo_path=i.photo_path,
        serial_number=i.serial_number,
        inventory_number=i.inventory_number,
        status=i.status,
        assigned_to_user_id=i.assigned_to_user_id,
        assigned_at=i.assigned_at,
        purchase_date=i.purchase_date,
        warranty_until=i.warranty_until,
        created_at=i.created_at,
        updated_at=i.updated_at,
        is_archived=i.is_archived,
    )


@router.get("/statuses", response_model=list[StatusItem])
async def list_statuses():
    return [StatusItem(value=v, label=l) for v, l in STATUSES]


@router.get("", response_model=list[InventoryItemResponse])
async def list_items(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    category_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    assigned_to_user_id: Optional[int] = Query(None),
    include_archived: bool = Query(False),
    repo: InventoryRepositoryPort = Depends(get_repo),
):
    filters = ItemFilters(
        skip=skip,
        limit=limit,
        category_id=category_id,
        status=status,
        assigned_to_user_id=assigned_to_user_id,
        include_archived=include_archived,
    )
    uc = ListItemsUseCase(repo)
    items = await uc.execute(filters)
    return [_to_response(i) for i in items]


@router.get("/{item_uuid}", response_model=InventoryItemResponse)
async def get_item(item_uuid: str, repo: InventoryRepositoryPort = Depends(get_repo)):
    uc = GetItemUseCase(repo)
    i = await uc.execute(item_uuid)
    if not i:
        raise HTTPException(status_code=404, detail="Item not found")
    return _to_response(i)


@router.post("", response_model=InventoryItemResponse, status_code=201)
async def create_item(
    name: str = Form(...),
    category_id: int = Form(...),
    inventory_number: str = Form(...),
    description: Optional[str] = Form(None),
    serial_number: Optional[str] = Form(None),
    status: str = Form("in_stock"),
    purchase_date: Optional[str] = Form(None),
    warranty_until: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    repo: InventoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    valid_statuses = [s[0] for s in STATUSES]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"status must be one of: {valid_statuses}")
    from datetime import datetime as dt
    purchase_dt = None
    if purchase_date:
        try:
            purchase_dt = dt.fromisoformat(purchase_date.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    warranty_dt = None
    if warranty_until:
        try:
            warranty_dt = dt.fromisoformat(warranty_until.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    photo_path = None
    if photo and photo.filename:
        try:
            content = await photo.read()
            photo_path = save_photo(photo.filename, content)
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e))
    uc = CreateItemUseCase(repo)
    item = await uc.execute(
        name=name,
        category_id=category_id,
        inventory_number=inventory_number,
        description=description or None,
        serial_number=serial_number or None,
        status=status,
        photo_path=photo_path,
        purchase_date=purchase_dt,
        warranty_until=warranty_dt,
    )
    await session.commit()
    return _to_response(item)


@router.patch("/{item_uuid}", response_model=InventoryItemResponse)
async def update_item(
    item_uuid: str,
    body: InventoryItemUpdate,
    repo: InventoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    valid_statuses = [s[0] for s in STATUSES]
    if body.status is not None and body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"status must be one of: {valid_statuses}")
    uc = UpdateItemUseCase(repo)
    item = await uc.execute(
        item_uuid,
        name=body.name,
        description=body.description,
        category_id=body.category_id,
        serial_number=body.serial_number,
        status=body.status,
        purchase_date=body.purchase_date,
        warranty_until=body.warranty_until,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await session.commit()
    return _to_response(item)


@router.post("/{item_uuid}/assign", response_model=InventoryItemResponse)
async def assign_item(
    item_uuid: str,
    body: AssignRequest,
    repo: InventoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = AssignItemUseCase(repo)
    item = await uc.execute(item_uuid, body.user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await session.commit()
    return _to_response(item)


@router.post("/{item_uuid}/unassign", response_model=InventoryItemResponse)
async def unassign_item(
    item_uuid: str,
    repo: InventoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = UnassignItemUseCase(repo)
    item = await uc.execute(item_uuid)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await session.commit()
    return _to_response(item)


@router.patch("/{item_uuid}/archive", response_model=InventoryItemResponse)
async def archive_item(
    item_uuid: str,
    is_archived: bool = True,
    repo: InventoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = ArchiveItemUseCase(repo)
    item = await uc.execute(item_uuid, is_archived=is_archived)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await session.commit()
    return _to_response(item)


@router.post("/{item_uuid}/photo", response_model=InventoryItemResponse)
async def upload_item_photo(
    item_uuid: str,
    photo: UploadFile = File(...),
    repo: InventoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    if not photo.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    try:
        content = await photo.read()
        photo_path = save_photo(photo.filename, content)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))
    uc = UpdateItemUseCase(repo)
    item = await uc.execute(item_uuid, photo_path=photo_path)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await session.commit()
    return _to_response(item)


@router.delete("/{item_uuid}", status_code=204)
async def delete_item(
    item_uuid: str,
    repo: InventoryRepositoryPort = Depends(get_repo),
    session: AsyncSession = Depends(get_session),
):
    uc = DeleteItemUseCase(repo)
    ok = await uc.execute(item_uuid)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    await session.commit()
