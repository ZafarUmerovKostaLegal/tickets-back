

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database import Base


class ExpenseKlSequenceModel(Base):


    __tablename__ = "expense_kl_sequence"

    singleton: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ExpenseRequestModel(Base):
    __tablename__ = "expense_requests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payment_deadline: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    amount_uzs: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    equivalent_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    expense_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expense_subtype: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_reimbursable: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    payment_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    expense_category_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    vendor: Mapped[str | None] = mapped_column(String(512), nullable=True)
    business_purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_approver_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    updated_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attachments: Mapped[list["ExpenseAttachmentModel"]] = relationship(
        "ExpenseAttachmentModel",
        back_populates="request",
        cascade="all, delete-orphan",
    )
    status_history: Mapped[list["ExpenseStatusHistoryModel"]] = relationship(
        "ExpenseStatusHistoryModel",
        back_populates="request",
        cascade="all, delete-orphan",
    )
    audit_logs: Mapped[list["ExpenseAuditLogModel"]] = relationship(
        "ExpenseAuditLogModel",
        back_populates="request",
        cascade="all, delete-orphan",
    )


class ExpenseAttachmentModel(Base):
    __tablename__ = "expense_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    expense_request_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("expense_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    attachment_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request: Mapped["ExpenseRequestModel"] = relationship("ExpenseRequestModel", back_populates="attachments")


class ExpenseStatusHistoryModel(Base):
    __tablename__ = "expense_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    expense_request_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("expense_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request: Mapped["ExpenseRequestModel"] = relationship("ExpenseRequestModel", back_populates="status_history")


class ExpenseAuditLogModel(Base):
    __tablename__ = "expense_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    expense_request_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("expense_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    performed_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request: Mapped["ExpenseRequestModel"] = relationship("ExpenseRequestModel", back_populates="audit_logs")


class ExpenseTypeModel(Base):
    __tablename__ = "expense_types"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DepartmentModel(Base):
    __tablename__ = "expense_departments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)


class ProjectModel(Base):
    __tablename__ = "expense_projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)


class ExchangeRateModel(Base):
    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    pair_label: Mapped[str] = mapped_column(String(32), nullable=False, default="UZS/USD_equiv")
