

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database import Base


class InvoiceCounterModel(Base):


    __tablename__ = "time_tracking_invoice_counters"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


class InvoiceModel(Base):
    __tablename__ = "time_tracking_invoices"
    __table_args__ = (
        Index("ix_tt_invoices_client", "client_id"),
        Index("ix_tt_invoices_project", "project_id"),
        Index("ix_tt_invoices_status", "status"),
        Index("ix_tt_invoices_issue_date", "issue_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_clients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("time_tracking_client_projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")

    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    tax_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    tax2_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))

    client_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_auth_user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    line_items: Mapped[list["InvoiceLineItemModel"]] = relationship(
        "InvoiceLineItemModel",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    payments: Mapped[list["InvoicePaymentModel"]] = relationship(
        "InvoicePaymentModel",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    audit_logs: Mapped[list["InvoiceAuditLogModel"]] = relationship(
        "InvoiceAuditLogModel",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )


class InvoiceLineItemModel(Base):
    __tablename__ = "time_tracking_invoice_line_items"
    __table_args__ = (
        Index("ix_tt_inv_lines_invoice", "invoice_id"),
        Index("ix_tt_inv_lines_time_entry", "time_entry_id"),
        Index("ix_tt_inv_lines_expense", "expense_request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal(1))
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal(0))
    time_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expense_request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    invoice: Mapped["InvoiceModel"] = relationship("InvoiceModel", back_populates="line_items")


class InvoicePaymentModel(Base):
    __tablename__ = "time_tracking_invoice_payments"
    __table_args__ = (Index("ix_tt_inv_pay_invoice", "invoice_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by_auth_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    invoice: Mapped["InvoiceModel"] = relationship("InvoiceModel", back_populates="payments")


class InvoiceAuditLogModel(Base):
    __tablename__ = "time_tracking_invoice_audit_logs"
    __table_args__ = (Index("ix_tt_inv_audit_invoice", "invoice_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_auth_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    invoice: Mapped["InvoiceModel"] = relationship("InvoiceModel", back_populates="audit_logs")
