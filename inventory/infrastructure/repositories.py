from datetime import datetime
from typing import Optional, Sequence
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from domain.entities import Category, InventoryItem
from application.ports import (
    HealthRepositoryPort,
    CategoryRepositoryPort,
    InventoryRepositoryPort,
    ItemFilters,
)
from infrastructure.models import CategoryModel, InventoryItemModel


class HealthRepository(HealthRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def check(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


def _category_to_entity(m: CategoryModel) -> Category:
    return Category(
        id=m.id,
        name=m.name,
        description=m.description,
        parent_id=m.parent_id,
        sort_order=m.sort_order,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class CategoryRepository(CategoryRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        name: str,
        description: Optional[str] = None,
        sort_order: int = 0,
        parent_id: Optional[int] = None,
    ) -> Category:
        model = CategoryModel(
            name=name,
            description=description,
            parent_id=parent_id,
            sort_order=sort_order,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _category_to_entity(model)

    async def get_by_id(self, category_id: int) -> Optional[Category]:
        result = await self._session.execute(select(CategoryModel).where(CategoryModel.id == category_id))
        row = result.scalars().one_or_none()
        return _category_to_entity(row) if row else None

    async def get_all(self) -> Sequence[Category]:
        result = await self._session.execute(
            select(CategoryModel).order_by(
                CategoryModel.parent_id,
                CategoryModel.sort_order,
                CategoryModel.name,
            )
        )
        rows = result.scalars().all()
        return [_category_to_entity(r) for r in rows]

    async def update(
        self,
        category_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        sort_order: Optional[int] = None,
        parent_id: Optional[int] = None,
    ) -> Optional[Category]:
        result = await self._session.execute(select(CategoryModel).where(CategoryModel.id == category_id))
        model = result.scalars().one_or_none()
        if not model:
            return None
        if name is not None:
            model.name = name
        if description is not None:
            model.description = description
        if sort_order is not None:
            model.sort_order = sort_order
        if parent_id is not None:
            model.parent_id = parent_id
        await self._session.flush()
        await self._session.refresh(model)
        return _category_to_entity(model)

    async def delete(self, category_id: int) -> bool:
        result = await self._session.execute(select(CategoryModel).where(CategoryModel.id == category_id))
        model = result.scalars().one_or_none()
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True


def _item_to_entity(m: InventoryItemModel) -> InventoryItem:
    return InventoryItem(
        id=m.id,
        uuid=m.uuid,
        name=m.name,
        description=m.description,
        category_id=m.category_id,
        photo_path=m.photo_path,
        serial_number=m.serial_number,
        inventory_number=m.inventory_number,
        status=m.status,
        assigned_to_user_id=m.assigned_to_user_id,
        assigned_at=m.assigned_at,
        purchase_date=m.purchase_date,
        warranty_until=m.warranty_until,
        created_at=m.created_at,
        updated_at=m.updated_at,
        is_archived=m.is_archived,
    )


class InventoryRepository(InventoryRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        uuid: str,
        name: str,
        category_id: int,
        inventory_number: str,
        description: Optional[str] = None,
        photo_path: Optional[str] = None,
        serial_number: Optional[str] = None,
        status: str = "in_stock",
        assigned_to_user_id: Optional[int] = None,
        assigned_at: Optional[datetime] = None,
        purchase_date: Optional[datetime] = None,
        warranty_until: Optional[datetime] = None,
    ) -> InventoryItem:
        model = InventoryItemModel(
            uuid=uuid,
            name=name,
            category_id=category_id,
            inventory_number=inventory_number,
            description=description,
            photo_path=photo_path,
            serial_number=serial_number,
            status=status,
            assigned_to_user_id=assigned_to_user_id,
            assigned_at=assigned_at,
            purchase_date=purchase_date,
            warranty_until=warranty_until,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _item_to_entity(model)

    async def get_by_uuid(self, item_uuid: str) -> Optional[InventoryItem]:
        result = await self._session.execute(
            select(InventoryItemModel).where(InventoryItemModel.uuid == item_uuid)
        )
        row = result.scalars().one_or_none()
        return _item_to_entity(row) if row else None

    async def get_by_inventory_number(self, inventory_number: str) -> Optional[InventoryItem]:
        result = await self._session.execute(
            select(InventoryItemModel).where(InventoryItemModel.inventory_number == inventory_number)
        )
        row = result.scalars().one_or_none()
        return _item_to_entity(row) if row else None

    async def get_all(self, filters: ItemFilters) -> Sequence[InventoryItem]:
        q = select(InventoryItemModel).order_by(InventoryItemModel.created_at.desc())
        conditions = []
        if not filters.include_archived:
            conditions.append(InventoryItemModel.is_archived == False)
        if filters.category_id is not None:
            conditions.append(InventoryItemModel.category_id == filters.category_id)
        if filters.status is not None:
            conditions.append(InventoryItemModel.status == filters.status)
        if filters.assigned_to_user_id is not None:
            conditions.append(InventoryItemModel.assigned_to_user_id == filters.assigned_to_user_id)
        if conditions:
            q = q.where(and_(*conditions))
        q = q.offset(filters.skip).limit(filters.limit)
        result = await self._session.execute(q)
        rows = result.scalars().all()
        return [_item_to_entity(r) for r in rows]

    async def update(
        self,
        item_uuid: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        category_id: Optional[int] = None,
        photo_path: Optional[str] = None,
        serial_number: Optional[str] = None,
        status: Optional[str] = None,
        purchase_date: Optional[datetime] = None,
        warranty_until: Optional[datetime] = None,
    ) -> Optional[InventoryItem]:
        result = await self._session.execute(
            select(InventoryItemModel).where(InventoryItemModel.uuid == item_uuid)
        )
        model = result.scalars().one_or_none()
        if not model:
            return None
        if name is not None:
            model.name = name
        if description is not None:
            model.description = description
        if category_id is not None:
            model.category_id = category_id
        if photo_path is not None:
            model.photo_path = photo_path
        if serial_number is not None:
            model.serial_number = serial_number
        if status is not None:
            model.status = status
        if purchase_date is not None:
            model.purchase_date = purchase_date
        if warranty_until is not None:
            model.warranty_until = warranty_until
        await self._session.flush()
        await self._session.refresh(model)
        return _item_to_entity(model)

    async def assign(self, item_uuid: str, user_id: int, assigned_at: datetime) -> Optional[InventoryItem]:
        result = await self._session.execute(
            select(InventoryItemModel).where(InventoryItemModel.uuid == item_uuid)
        )
        model = result.scalars().one_or_none()
        if not model:
            return None
        model.assigned_to_user_id = user_id
        model.assigned_at = assigned_at
        model.status = "in_use"
        await self._session.flush()
        await self._session.refresh(model)
        return _item_to_entity(model)

    async def unassign(self, item_uuid: str) -> Optional[InventoryItem]:
        result = await self._session.execute(
            select(InventoryItemModel).where(InventoryItemModel.uuid == item_uuid)
        )
        model = result.scalars().one_or_none()
        if not model:
            return None
        model.assigned_to_user_id = None
        model.assigned_at = None
        model.status = "in_stock"
        await self._session.flush()
        await self._session.refresh(model)
        return _item_to_entity(model)

    async def set_archived(self, item_uuid: str, is_archived: bool) -> Optional[InventoryItem]:
        result = await self._session.execute(
            select(InventoryItemModel).where(InventoryItemModel.uuid == item_uuid)
        )
        model = result.scalars().one_or_none()
        if not model:
            return None
        model.is_archived = is_archived
        await self._session.flush()
        await self._session.refresh(model)
        return _item_to_entity(model)

    async def delete(self, item_uuid: str) -> bool:
        result = await self._session.execute(
            select(InventoryItemModel).where(InventoryItemModel.uuid == item_uuid)
        )
        model = result.scalars().one_or_none()
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True
