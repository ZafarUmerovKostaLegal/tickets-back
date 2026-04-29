

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvoiceLineCreateSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    line_kind: Literal["manual", "time", "expense"] = Field("manual", alias="lineKind")
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_amount: Optional[Decimal] = Field(None, alias="unitAmount")
    line_total: Optional[Decimal] = Field(None, alias="lineTotal")
    time_entry_id: Optional[str] = Field(None, alias="timeEntryId")
    expense_request_id: Optional[str] = Field(None, alias="expenseRequestId")


class InvoiceCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(..., alias="clientId")
    project_id: Optional[str] = Field(None, alias="projectId")
    issue_date: date = Field(..., alias="issueDate")
    due_date: date = Field(..., alias="dueDate")
    currency: Optional[str] = None
    tax_percent: Optional[Decimal] = Field(None, alias="taxPercent")
    tax2_percent: Optional[Decimal] = Field(None, alias="tax2Percent")
    discount_percent: Optional[Decimal] = Field(None, alias="discountPercent")
    client_note: Optional[str] = Field(None, alias="clientNote")
    internal_note: Optional[str] = Field(None, alias="internalNote")
    lines: Optional[list[InvoiceLineCreateSpec]] = None
    time_entry_ids: Optional[list[str]] = Field(None, alias="timeEntryIds")
    expense_ids: Optional[list[str]] = Field(None, alias="expenseIds")


class InvoicePatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    issue_date: Optional[date] = Field(None, alias="issueDate")
    due_date: Optional[date] = Field(None, alias="dueDate")
    client_note: Optional[str] = Field(None, alias="clientNote")
    internal_note: Optional[str] = Field(None, alias="internalNote")
    tax_percent: Optional[Decimal] = Field(None, alias="taxPercent")
    tax2_percent: Optional[Decimal] = Field(None, alias="tax2Percent")
    discount_percent: Optional[Decimal] = Field(None, alias="discountPercent")
    project_id: Optional[str] = Field(None, alias="projectId")
    lines: Optional[list[dict[str, Any]]] = None


class InvoicePaymentBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    amount: Optional[Decimal] = None
    paid_at: Optional[datetime] = Field(None, alias="paidAt")
    payment_method: Optional[str] = Field(None, alias="paymentMethod")
    note: Optional[str] = None

    @field_validator("amount", mode="before")
    @classmethod
    def _normalize_amount(cls, v: Any) -> Any:
        if v is None or isinstance(v, (int, float, Decimal)):
            return v
        if isinstance(v, str):
            s = v.strip().replace(" ", "").replace("\u00a0", "")
            if not s:
                return None
            if "," in s and "." not in s:
                s = s.replace(",", ".")
            elif "," in s and "." in s:
                if s.rfind(",") > s.rfind("."):
                    s = s.replace(".", "").replace(",", ".")
                else:
                    s = s.replace(",", "")
            return s
        return v
