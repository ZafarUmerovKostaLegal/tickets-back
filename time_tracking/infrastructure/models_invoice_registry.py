from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base


class InvoiceRegistryRowModel(Base):
    __tablename__ = "invoice_registry_rows"
    __table_args__ = (
        Index("ix_inv_reg_year", "year"),
        Index("ix_inv_reg_partner", "partner"),
        Index("ix_inv_reg_currency", "currency"),
        Index("ix_inv_reg_client_number", "client_number"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, default=2026)
    seq_no: Mapped[str] = mapped_column(Text, nullable=False, default="")
    billed_to: Mapped[str] = mapped_column(Text, nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    amount: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    partner: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    issue_date: Mapped[str] = mapped_column(Text, nullable=False, default="")
    due_or_payment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    client_number: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    advance_fee: Mapped[str] = mapped_column(Text, nullable=False, default="")
    balance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvoiceRegistryArchiveSheetModel(Base):
    __tablename__ = "invoice_registry_archive_sheets"

    year_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

