

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TimeManagerClientTaskCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=500)
    default_billable_rate: Optional[Decimal] = Field(None, alias="defaultBillableRate", ge=0)
    billable_by_default: bool = Field(True, alias="billableByDefault")
    common_for_future_projects: bool = Field(False, alias="commonForFutureProjects")
    add_to_existing_projects: bool = Field(False, alias="addToExistingProjects")


class TimeManagerClientTaskPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, max_length=500)
    default_billable_rate: Optional[Decimal] = Field(None, alias="defaultBillableRate", ge=0)
    billable_by_default: Optional[bool] = Field(None, alias="billableByDefault")
    common_for_future_projects: Optional[bool] = Field(None, alias="commonForFutureProjects")
    add_to_existing_projects: Optional[bool] = Field(None, alias="addToExistingProjects")
