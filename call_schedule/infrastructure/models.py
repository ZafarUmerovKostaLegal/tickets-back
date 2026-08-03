from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base


class CallScheduleDayFileModel(Base):
    __tablename__ = "call_schedule_day_files"
    __table_args__ = (
        Index("ix_call_schedule_day_files_day", "day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    uploaded_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
