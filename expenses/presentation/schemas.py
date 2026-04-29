from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from application.expense_service import normalize_payment_method, validate_expense_subtype_rules, validate_expense_type

MoneyDecimal = Annotated[
    Decimal,
    Field(json_schema_extra={"example": 1250.5}),
]


def _coerce_decimal_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        raise ValueError("Некорректное числовое значение")
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, dict):
        if "$numberDecimal" in v:
            return Decimal(str(v["$numberDecimal"]))
        if "value" in v:
            return _coerce_decimal_value(v["value"])
    if isinstance(v, str):
        s = v.replace(",", ".").replace(" ", "").replace("\u00a0", "").strip()
        if not s:
            return None
        return Decimal(s)
    raise ValueError("Некорректный формат числа")


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


class AttachmentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    expense_request_id: str = Field(serialization_alias="expenseRequestId")
    file_name: str = Field(serialization_alias="fileName")
    storage_key: str = Field(serialization_alias="storageKey")
    mime_type: Optional[str] = Field(None, serialization_alias="mimeType")
    size_bytes: int = Field(serialization_alias="sizeBytes")
    attachment_kind: Optional[str] = Field(None, serialization_alias="attachmentKind")
    uploaded_by_user_id: int = Field(serialization_alias="uploadedByUserId")
    uploaded_at: datetime = Field(serialization_alias="uploadedAt")


class StatusHistoryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    expense_request_id: str = Field(serialization_alias="expenseRequestId")
    from_status: Optional[str] = Field(None, serialization_alias="fromStatus")
    to_status: str = Field(serialization_alias="toStatus")
    changed_by_user_id: int = Field(serialization_alias="changedByUserId")
    comment: Optional[str] = None
    changed_at: datetime = Field(serialization_alias="changedAt")


class AuditLogOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    expense_request_id: str = Field(serialization_alias="expenseRequestId")
    action: str
    field_name: Optional[str] = Field(None, serialization_alias="fieldName")
    old_value: Optional[str] = Field(None, serialization_alias="oldValue")
    new_value: Optional[str] = Field(None, serialization_alias="newValue")
    performed_by_user_id: int = Field(serialization_alias="performedByUserId")
    performed_at: datetime = Field(serialization_alias="performedAt")


class ExpenseAuthorSnippet(BaseModel):


    model_config = ConfigDict(populate_by_name=True)

    id: int
    display_name: Optional[str] = Field(None, serialization_alias="displayName")
    email: Optional[str] = None
    picture: Optional[str] = None
    position: Optional[str] = None


class ExpenseRequestListItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    description: str
    expense_date: date = Field(serialization_alias="expenseDate")
    payment_deadline: Optional[date] = Field(None, serialization_alias="paymentDeadline")
    amount_uzs: MoneyDecimal = Field(serialization_alias="amountUzs")
    exchange_rate: MoneyDecimal = Field(serialization_alias="exchangeRate")
    equivalent_amount: MoneyDecimal = Field(serialization_alias="equivalentAmount")
    expense_type: str = Field(serialization_alias="expenseType")
    expense_subtype: Optional[str] = Field(None, serialization_alias="expenseSubtype")
    is_reimbursable: bool = Field(serialization_alias="isReimbursable")
    payment_method: Optional[str] = Field(None, serialization_alias="paymentMethod")
    department_id: Optional[str] = Field(None, serialization_alias="departmentId")
    project_id: Optional[str] = Field(None, serialization_alias="projectId")
    expense_category_id: Optional[str] = Field(None, serialization_alias="expenseCategoryId")
    vendor: Optional[str] = None
    business_purpose: Optional[str] = Field(None, serialization_alias="businessPurpose")
    comment: Optional[str] = None
    status: str
    current_approver_id: Optional[int] = Field(None, serialization_alias="currentApproverId")
    created_by_user_id: int = Field(serialization_alias="createdByUserId")
    created_by: ExpenseAuthorSnippet = Field(serialization_alias="createdBy")
    updated_by_user_id: int = Field(serialization_alias="updatedByUserId")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
    submitted_at: Optional[datetime] = Field(None, serialization_alias="submittedAt")
    approved_at: Optional[datetime] = Field(None, serialization_alias="approvedAt")
    rejected_at: Optional[datetime] = Field(None, serialization_alias="rejectedAt")
    paid_at: Optional[datetime] = Field(None, serialization_alias="paidAt")
    paid_by_user_id: Optional[int] = Field(None, serialization_alias="paidByUserId")
    paid_by: Optional[ExpenseAuthorSnippet] = Field(None, serialization_alias="paidBy")
    closed_at: Optional[datetime] = Field(None, serialization_alias="closedAt")
    withdrawn_at: Optional[datetime] = Field(None, serialization_alias="withdrawnAt")
    attachments_count: int = Field(0, serialization_alias="attachmentsCount")
    payment_document_uploaded: bool = Field(False, serialization_alias="paymentDocumentUploaded")
    payment_receipt_uploaded: bool = Field(False, serialization_alias="paymentReceiptUploaded")


class ExpenseRequestDetailOut(ExpenseRequestListItemOut):
    attachments: list[AttachmentOut] = Field(default_factory=list)
    status_history: list[StatusHistoryOut] = Field(
        default_factory=list,
        serialization_alias="statusHistory",
    )
    audit_logs: list[AuditLogOut] = Field(default_factory=list, serialization_alias="auditLog")


class ExpenseListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[ExpenseRequestListItemOut]
    total: int
    skip: int
    limit: int


class ExpenseCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: str = Field(..., min_length=1)
    expense_date: date = Field(..., validation_alias=AliasChoices("expenseDate", "expense_date"))
    payment_deadline: Optional[date] = Field(
        None, validation_alias=AliasChoices("paymentDeadline", "payment_deadline")
    )
    amount_uzs: MoneyDecimal = Field(..., validation_alias=AliasChoices("amountUzs", "amount_uzs"))
    exchange_rate: MoneyDecimal = Field(..., validation_alias=AliasChoices("exchangeRate", "exchange_rate"))
    expense_type: str = Field(..., validation_alias=AliasChoices("expenseType", "expense_type"))
    is_reimbursable: bool = Field(..., validation_alias=AliasChoices("isReimbursable", "is_reimbursable"))
    expense_subtype: Optional[str] = Field(None, validation_alias=AliasChoices("expenseSubtype", "expense_subtype"))
    payment_method: Optional[str] = Field(None, validation_alias=AliasChoices("paymentMethod", "payment_method"))
    department_id: Optional[str] = Field(None, validation_alias=AliasChoices("departmentId", "department_id"))
    project_id: Optional[str] = Field(None, validation_alias=AliasChoices("projectId", "project_id"))
    expense_category_id: Optional[str] = Field(None, validation_alias=AliasChoices("expenseCategoryId", "expense_category_id"))
    vendor: Optional[str] = None
    business_purpose: Optional[str] = Field(None, validation_alias=AliasChoices("businessPurpose", "business_purpose"))
    comment: Optional[str] = None
    current_approver_id: Optional[int] = Field(None, validation_alias=AliasChoices("currentApproverId", "current_approver_id"))

    @field_validator("amount_uzs", "exchange_rate", mode="before")
    @classmethod
    def coerce_money(cls, v: Any) -> Any:
        return _coerce_decimal_value(v)

    @field_validator("expense_type", mode="after")
    @classmethod
    def check_expense_type(cls, v: str) -> str:
        return validate_expense_type(v)

    @field_validator("payment_method", mode="before")
    @classmethod
    def coerce_payment_method(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return normalize_payment_method(str(v))

    @model_validator(mode="after")
    def validate_deadline_vs_expense_date(self) -> "ExpenseCreateBody":
        if self.payment_deadline is not None and self.payment_deadline < self.expense_date:
            raise ValueError("Конечный срок оплаты не может быть раньше даты расхода")
        return self

    @model_validator(mode="after")
    def validate_partner_subtype_create(self) -> "ExpenseCreateBody":
        validate_expense_subtype_rules(self.expense_type, self.expense_subtype)
        return self


class ExpenseUpdateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: Optional[str] = None
    expense_date: Optional[date] = Field(None, validation_alias=AliasChoices("expenseDate", "expense_date"))
    payment_deadline: Optional[date] = Field(
        None, validation_alias=AliasChoices("paymentDeadline", "payment_deadline")
    )
    amount_uzs: Optional[MoneyDecimal] = Field(None, validation_alias=AliasChoices("amountUzs", "amount_uzs"))
    exchange_rate: Optional[MoneyDecimal] = Field(None, validation_alias=AliasChoices("exchangeRate", "exchange_rate"))
    expense_type: Optional[str] = Field(None, validation_alias=AliasChoices("expenseType", "expense_type"))
    expense_subtype: Optional[str] = Field(None, validation_alias=AliasChoices("expenseSubtype", "expense_subtype"))
    is_reimbursable: Optional[bool] = Field(None, validation_alias=AliasChoices("isReimbursable", "is_reimbursable"))
    payment_method: Optional[str] = Field(None, validation_alias=AliasChoices("paymentMethod", "payment_method"))
    department_id: Optional[str] = Field(None, validation_alias=AliasChoices("departmentId", "department_id"))
    project_id: Optional[str] = Field(None, validation_alias=AliasChoices("projectId", "project_id"))
    expense_category_id: Optional[str] = Field(None, validation_alias=AliasChoices("expenseCategoryId", "expense_category_id"))
    vendor: Optional[str] = None
    business_purpose: Optional[str] = Field(None, validation_alias=AliasChoices("businessPurpose", "business_purpose"))
    comment: Optional[str] = None
    current_approver_id: Optional[int] = Field(None, validation_alias=AliasChoices("currentApproverId", "current_approver_id"))

    @field_validator("amount_uzs", "exchange_rate", mode="before")
    @classmethod
    def coerce_money_opt(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return _coerce_decimal_value(v)

    @field_validator("expense_type", mode="after")
    @classmethod
    def check_expense_type_opt(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return validate_expense_type(v)

    @field_validator("payment_method", mode="before")
    @classmethod
    def coerce_payment_method_opt(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return normalize_payment_method(str(v))


class RejectBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(..., min_length=1, validation_alias=AliasChoices("reason", "rejectionReason", "rejection_reason"))

    @field_validator("reason", mode="after")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("reason must not be empty")
        return s


class ReviseBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    comment: str = Field(..., min_length=1)

    @field_validator("comment", mode="after")
    @classmethod
    def strip_comment(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("comment must not be empty")
        return s


class ExpenseTypeRefOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    label: str
    sort_order: int = Field(serialization_alias="sortOrder")


class DepartmentRefOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str


class ProjectRefOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str


class ExchangeRateOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date: date
    rate: MoneyDecimal
    pair_label: str = Field(serialization_alias="pairLabel")
