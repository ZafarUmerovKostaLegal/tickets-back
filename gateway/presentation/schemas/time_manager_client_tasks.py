
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TimeManagerClientTaskCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=500)
    default_billable_rate: Optional[Decimal] = Field(None, alias="defaultBillableRate", ge=0)
    billable_by_default: bool = Field(True, alias="billableByDefault")
    billing_mode: str = Field("hourly", alias="billingMode")
    flat_fee_amount: Optional[Decimal] = Field(None, alias="flatFeeAmount", ge=0)
    flat_fee_currency: Optional[str] = Field(None, alias="flatFeeCurrency", max_length=10)


class TimeManagerClientTaskPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, max_length=500)
    default_billable_rate: Optional[Decimal] = Field(None, alias="defaultBillableRate", ge=0)
    billable_by_default: Optional[bool] = Field(None, alias="billableByDefault")
    billing_mode: Optional[str] = Field(None, alias="billingMode")
    flat_fee_amount: Optional[Decimal] = Field(None, alias="flatFeeAmount", ge=0)
    flat_fee_currency: Optional[str] = Field(None, alias="flatFeeCurrency", max_length=10)
