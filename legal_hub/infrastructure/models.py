from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base


class LegalHubTaskModel(Base):
    __tablename__ = "legal_hub_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    assignee_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    reviewer_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_label: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
