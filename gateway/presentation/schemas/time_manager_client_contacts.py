

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TimeManagerClientContactCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=500)
    phone: Optional[str] = Field(None, max_length=64)
    email: Optional[str] = Field(None, max_length=320)
    sort_order: Optional[int] = Field(None, alias="sortOrder")


class TimeManagerClientContactPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, max_length=500)
    phone: Optional[str] = Field(None, max_length=64)
    email: Optional[str] = Field(None, max_length=320)
    sort_order: Optional[int] = Field(None, alias="sortOrder")
