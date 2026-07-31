from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base


class FirmBankProfileModel(Base):
    """Firm banking requisites used on invoice legal page / letters."""

    __tablename__ = "time_tracking_firm_bank_profiles"
    __table_args__ = (
        Index("ix_tt_firm_bank_default", "is_default"),
        Index("ix_tt_firm_bank_currency", "account_currency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default=text("''"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    tin: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    bank_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    bank_address: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    account_currency: Mapped[str] = mapped_column(String(16), nullable=False, default="EUR", server_default=text("'EUR'"))
    account_number: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    bank_code: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    swift: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    correspondent_bank: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    correspondent_account: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_by_auth_user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
