from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, Field

EquipmentClass = Literal["A", "B", "C", "D", "E"]

class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


class CategoryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    parent_id: Optional[int] = None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sort_order: int = 0
    parent_id: Optional[int] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    parent_id: Optional[int] = None


class InventoryItemResponse(BaseModel):
    id: int
    uuid: str
    name: str
    description: Optional[str] = None
    category_id: int
    photo_path: Optional[str] = None
    serial_number: Optional[str] = None
    inventory_number: str
    equipment_class: Optional[EquipmentClass] = None
    status: str
    assigned_to_user_id: Optional[int] = None
    assigned_at: Optional[datetime] = None
    purchase_date: Optional[datetime] = None
    warranty_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    is_archived: bool


class InventoryItemListResponse(BaseModel):
    items: list[InventoryItemResponse]
    total: int
    skip: int
    limit: int
    in_use_count: int = 0
    in_stock_count: int = 0
    archived_count: int = 0


class InventoryItemCreate(BaseModel):
    name: str
    category_id: int
    inventory_number: str
    description: Optional[str] = None
    serial_number: Optional[str] = None
    equipment_class: Optional[EquipmentClass] = None
    status: str = "in_stock"
    purchase_date: Optional[datetime] = None
    warranty_until: Optional[datetime] = None


class InventoryItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    serial_number: Optional[str] = None
    inventory_number: Optional[str] = None
    equipment_class: Optional[EquipmentClass] = None
    status: Optional[str] = None
    purchase_date: Optional[datetime] = None
    warranty_until: Optional[datetime] = None


class AssignRequest(BaseModel):
    user_id: int


class StatusItem(BaseModel):
    value: str
    label: str
