

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TimeManagerClientExpenseCategoryCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=500)
    has_unit_price: bool = Field(False, alias="hasUnitPrice")
    sort_order: Optional[int] = Field(None, alias="sortOrder")


class TimeManagerClientExpenseCategoryPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, max_length=500)
    has_unit_price: Optional[bool] = Field(None, alias="hasUnitPrice")
    is_archived: Optional[bool] = Field(None, alias="isArchived")
    sort_order: Optional[int] = Field(None, alias="sortOrder")
