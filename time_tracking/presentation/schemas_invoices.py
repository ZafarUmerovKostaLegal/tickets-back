

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


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
    invoice_number: Optional[str] = Field(
        None,
        alias="invoiceNumber",
        max_length=64,
        description="Номер счёта; если не задан — автогенерация INV-{year}-{seq}",
    )
    currency: Optional[str] = None
    tax_percent: Optional[Decimal] = Field(None, alias="taxPercent")
    tax2_percent: Optional[Decimal] = Field(None, alias="tax2Percent")
    discount_percent: Optional[Decimal] = Field(None, alias="discountPercent")
    client_note: Optional[str] = Field(None, alias="clientNote")
    internal_note: Optional[str] = Field(None, alias="internalNote")
    lines: Optional[list[InvoiceLineCreateSpec]] = None
    time_entry_ids: Optional[list[str]] = Field(None, alias="timeEntryIds")
    expense_ids: Optional[list[str]] = Field(None, alias="expenseIds")
    partner_billing_period_from: Optional[date] = Field(
        None,
        alias="partnerBillingPeriodFrom",
        description="Начало периода биллинга; весь интервал [from, to] должен входить в подтверждённый период",
    )
    partner_billing_period_to: Optional[date] = Field(
        None,
        alias="partnerBillingPeriodTo",
        description="Конец периода биллинга; вместе с from — подмножество подтверждённых дат по проекту",
    )
    partner_confirmation_request_id: Optional[str] = Field(
        None,
        alias="partnerConfirmationRequestId",
        description="ID запроса подтверждения партнёров, из которого создан счёт",
    )

    @field_validator("invoice_number", mode="after")
    @classmethod
    def _strip_invoice_number(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        return s or None

    @field_validator("partner_confirmation_request_id", mode="after")
    @classmethod
    def _strip_partner_confirmation_request_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        return s or None


class PartnerInvoicePreviewQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(..., alias="projectId")
    date_from: date = Field(..., alias="dateFrom")
    date_to: date = Field(..., alias="dateTo")
    currency: Optional[str] = None
    issue_date: Optional[date] = Field(None, alias="issueDate")
    client_id: Optional[str] = Field(None, alias="clientId")


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


class InvoicePaymentConfirmationBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_url: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        validation_alias=AliasChoices("documentUrl", "document_url"),
        description="Ссылка или идентификатор документа, подтверждающего оплату",
    )

    @field_validator("document_url", mode="after")
    @classmethod
    def _strip_document_url(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("document_url")
        return s


class InvoiceAccountingLastPageNotifyBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pdf_base64: str = Field(
        ...,
        min_length=1,
        alias="pdfBase64",
        description="PDF последней страницы счёта (base64, опционально data-URL)",
    )
    pdf_file_name: Optional[str] = Field(None, alias="pdfFileName")
    client_name: Optional[str] = Field(None, alias="clientName")

    @field_validator("pdf_base64", mode="after")
    @classmethod
    def _strip_pdf_base64(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("pdfBase64")
        return s

    @field_validator("pdf_file_name", "client_name", mode="after")
    @classmethod
    def _strip_optional_str(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        return s or None


class InvoicePaymentBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    amount: Optional[Decimal] = None
    paid_at: Optional[datetime] = Field(None, alias="paidAt")
    payment_method: Optional[str] = Field(None, alias="paymentMethod")
    note: Optional[str] = None

    @field_validator("paid_at", mode="before")
    @classmethod
    def _normalize_paid_at(cls, v: Any) -> Any:
        """Дата из UI часто приходит как DD.MM.YYYY HH:MM — без этого Pydantic отклоняет тело (422)."""
        if v is None or isinstance(v, datetime):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            naive_formats = (
                "%d.%m.%Y %H:%M",
                "%d.%m.%Y %H:%M:%S",
                "%d.%m.%Y",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
            )
            for fmt in naive_formats:
                try:
                    dt = datetime.strptime(s, fmt)
                    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                except ValueError:
                    continue
            try:
                raw = s.replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw)
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
            except ValueError:
                pass
        return v

    @field_validator("payment_method", "note", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

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
