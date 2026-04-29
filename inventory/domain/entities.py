from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class HealthEntity:
    status: str
    service: str
    timestamp: datetime


@dataclass
class Category:
    id: int
    name: str
    description: Optional[str]
    parent_id: Optional[int]
    sort_order: int
    created_at: datetime
    updated_at: datetime


@dataclass
class InventoryItem:
    id: int
    uuid: str
    name: str
    description: Optional[str]
    category_id: int
    photo_path: Optional[str]
    serial_number: Optional[str]
    inventory_number: str
    status: str
    assigned_to_user_id: Optional[int]
    assigned_at: Optional[datetime]
    purchase_date: Optional[datetime]
    warranty_until: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    is_archived: bool
