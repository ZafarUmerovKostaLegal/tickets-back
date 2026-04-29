import uuid as uuid_lib
from datetime import datetime
from typing import Optional, Sequence
from domain.entities import Category, InventoryItem
from application.ports import (
    HealthRepositoryPort,
    CategoryRepositoryPort,
    InventoryRepositoryPort,
    ItemFilters,
)


class GetHealthUseCase:
    def __init__(self, health_repo: HealthRepositoryPort):
        self._health_repo = health_repo

    async def execute(self, service_name: str):
        from domain.entities import HealthEntity
        db_ok = await self._health_repo.check()
        status = "healthy" if db_ok else "degraded"
        return HealthEntity(
            status=status,
            service=service_name,
            timestamp=datetime.utcnow(),
        )


class CreateCategoryUseCase:
    def __init__(self, repo: CategoryRepositoryPort):
        self._repo = repo

    async def execute(
        self,
        name: str,
        description: Optional[str] = None,
        sort_order: int = 0,
        parent_id: Optional[int] = None,
    ) -> Category:
        return await self._repo.create(
            name=name,
            description=description,
            sort_order=sort_order,
            parent_id=parent_id,
        )


class ListCategoriesUseCase:
    def __init__(self, repo: CategoryRepositoryPort):
        self._repo = repo

    async def execute(self) -> Sequence[Category]:
        return await self._repo.get_all()


class GetCategoryUseCase:
    def __init__(self, repo: CategoryRepositoryPort):
        self._repo = repo

    async def execute(self, category_id: int) -> Optional[Category]:
        return await self._repo.get_by_id(category_id)


class UpdateCategoryUseCase:
    def __init__(self, repo: CategoryRepositoryPort):
        self._repo = repo

    async def execute(
        self,
        category_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        sort_order: Optional[int] = None,
        parent_id: Optional[int] = None,
    ) -> Optional[Category]:
        return await self._repo.update(
            category_id,
            name=name,
            description=description,
            sort_order=sort_order,
            parent_id=parent_id,
        )


class DeleteCategoryUseCase:
    def __init__(self, repo: CategoryRepositoryPort):
        self._repo = repo

    async def execute(self, category_id: int) -> bool:
        return await self._repo.delete(category_id)


class CreateItemUseCase:
    def __init__(self, repo: InventoryRepositoryPort):
        self._repo = repo

    async def execute(
        self,
        name: str,
        category_id: int,
        inventory_number: str,
        description: Optional[str] = None,
        photo_path: Optional[str] = None,
        serial_number: Optional[str] = None,
        status: str = "in_stock",
        purchase_date: Optional[datetime] = None,
        warranty_until: Optional[datetime] = None,
    ) -> InventoryItem:
        item_uuid = str(uuid_lib.uuid4())
        return await self._repo.create(
            uuid=item_uuid,
            name=name,
            category_id=category_id,
            inventory_number=inventory_number,
            description=description,
            photo_path=photo_path,
            serial_number=serial_number,
            status=status,
            purchase_date=purchase_date,
            warranty_until=warranty_until,
        )


class GetItemUseCase:
    def __init__(self, repo: InventoryRepositoryPort):
        self._repo = repo

    async def execute(self, item_uuid: str) -> Optional[InventoryItem]:
        return await self._repo.get_by_uuid(item_uuid)


class ListItemsUseCase:
    def __init__(self, repo: InventoryRepositoryPort):
        self._repo = repo

    async def execute(self, filters: ItemFilters) -> Sequence[InventoryItem]:
        return await self._repo.get_all(filters)


class UpdateItemUseCase:
    def __init__(self, repo: InventoryRepositoryPort):
        self._repo = repo

    async def execute(
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
        return await self._repo.update(
            item_uuid,
            name=name,
            description=description,
            category_id=category_id,
            photo_path=photo_path,
            serial_number=serial_number,
            status=status,
            purchase_date=purchase_date,
            warranty_until=warranty_until,
        )


class AssignItemUseCase:
    def __init__(self, repo: InventoryRepositoryPort):
        self._repo = repo

    async def execute(self, item_uuid: str, user_id: int) -> Optional[InventoryItem]:
        return await self._repo.assign(item_uuid, user_id, datetime.utcnow())


class UnassignItemUseCase:
    def __init__(self, repo: InventoryRepositoryPort):
        self._repo = repo

    async def execute(self, item_uuid: str) -> Optional[InventoryItem]:
        return await self._repo.unassign(item_uuid)


class ArchiveItemUseCase:
    def __init__(self, repo: InventoryRepositoryPort):
        self._repo = repo

    async def execute(self, item_uuid: str, is_archived: bool = True) -> Optional[InventoryItem]:
        return await self._repo.set_archived(item_uuid, is_archived)


class DeleteItemUseCase:
    def __init__(self, repo: InventoryRepositoryPort):
        self._repo = repo

    async def execute(self, item_uuid: str) -> bool:
        return await self._repo.delete(item_uuid)
