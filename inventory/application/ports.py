from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Sequence
from domain.entities import Category, InventoryItem


class HealthRepositoryPort(ABC):
    @abstractmethod
    async def check(self) -> bool:
        pass


class CategoryRepositoryPort(ABC):
    @abstractmethod
    async def create(
        self,
        name: str,
        description: Optional[str] = None,
        sort_order: int = 0,
        parent_id: Optional[int] = None,
    ) -> Category:
        pass

    @abstractmethod
    async def get_by_id(self, category_id: int) -> Optional[Category]:
        pass

    @abstractmethod
    async def get_all(self) -> Sequence[Category]:
        pass

    @abstractmethod
    async def update(
        self,
        category_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        sort_order: Optional[int] = None,
        parent_id: Optional[int] = None,
    ) -> Optional[Category]:
        pass

    @abstractmethod
    async def delete(self, category_id: int) -> bool:
        pass


class ItemFilters:
    def __init__(
        self,
        skip: int = 0,
        limit: int = 50,
        category_id: Optional[int] = None,
        status: Optional[str] = None,
        assigned_to_user_id: Optional[int] = None,
        include_archived: bool = False,
    ):
        self.skip = skip
        self.limit = limit
        self.category_id = category_id
        self.status = status
        self.assigned_to_user_id = assigned_to_user_id
        self.include_archived = include_archived


class InventoryRepositoryPort(ABC):
    @abstractmethod
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
        pass

    @abstractmethod
    async def get_by_uuid(self, item_uuid: str) -> Optional[InventoryItem]:
        pass

    @abstractmethod
    async def get_by_inventory_number(self, inventory_number: str) -> Optional[InventoryItem]:
        pass

    @abstractmethod
    async def get_all(self, filters: ItemFilters) -> Sequence[InventoryItem]:
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def assign(self, item_uuid: str, user_id: int, assigned_at: datetime) -> Optional[InventoryItem]:
        pass

    @abstractmethod
    async def unassign(self, item_uuid: str) -> Optional[InventoryItem]:
        pass

    @abstractmethod
    async def set_archived(self, item_uuid: str, is_archived: bool) -> Optional[InventoryItem]:
        pass

    @abstractmethod
    async def delete(self, item_uuid: str) -> bool:
        pass
