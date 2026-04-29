

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TimeManagerClientCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=500)
    address: Optional[str] = None
    currency: str = Field("USD", max_length=10)
    invoice_due_mode: str = Field("custom", alias="invoiceDueMode", max_length=50)
    invoice_due_days_after_issue: Optional[int] = Field(
        None,
        alias="invoiceDueDaysAfterIssue",
        ge=0,
        le=3650,
    )
    tax_percent: Optional[Decimal] = Field(None, alias="taxPercent", ge=0, le=100)
    tax2_percent: Optional[Decimal] = Field(None, alias="tax2Percent", ge=0, le=100)
    discount_percent: Optional[Decimal] = Field(None, alias="discountPercent", ge=0, le=100)
    phone: Optional[str] = Field(None, max_length=64)
    email: Optional[str] = Field(None, max_length=320)
    contact_name: Optional[str] = Field(None, alias="contactName", max_length=500)
    contact_phone: Optional[str] = Field(None, alias="contactPhone", max_length=64)
    contact_email: Optional[str] = Field(None, alias="contactEmail", max_length=320)
    is_archived: bool = Field(False, alias="isArchived")


class TimeManagerClientPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, max_length=500)
    address: Optional[str] = None
    currency: Optional[str] = Field(None, max_length=10)
    invoice_due_mode: Optional[str] = Field(None, alias="invoiceDueMode", max_length=50)
    invoice_due_days_after_issue: Optional[int] = Field(
        None,
        alias="invoiceDueDaysAfterIssue",
        ge=0,
        le=3650,
    )
    tax_percent: Optional[Decimal] = Field(None, alias="taxPercent", ge=0, le=100)
    tax2_percent: Optional[Decimal] = Field(None, alias="tax2Percent", ge=0, le=100)
    discount_percent: Optional[Decimal] = Field(None, alias="discountPercent", ge=0, le=100)
    phone: Optional[str] = Field(None, max_length=64)
    email: Optional[str] = Field(None, max_length=320)
    contact_name: Optional[str] = Field(None, alias="contactName", max_length=500)
    contact_phone: Optional[str] = Field(None, alias="contactPhone", max_length=64)
    contact_email: Optional[str] = Field(None, alias="contactEmail", max_length=320)
    is_archived: Optional[bool] = Field(None, alias="isArchived")
