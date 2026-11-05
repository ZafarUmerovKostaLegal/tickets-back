from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, JSON, String, Text
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


class LegalHubBoardColumnsModel(Base):
    __tablename__ = "legal_hub_board_columns"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    custom_columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    column_order: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    collapsed: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LegalHubProjectFoldersModel(Base):
    __tablename__ = "legal_hub_project_folders"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hide_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    folders: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LegalHubBoardFilterModel(Base):
    __tablename__ = "legal_hub_board_filters"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    only_late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    only_urgent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    project: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    stream: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
