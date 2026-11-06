from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, JSON, LargeBinary, String, Text
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
    project_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    internal_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    board_column: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    important: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assignee_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    assignee_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LegalHubTaskCommentModel(Base):
    __tablename__ = "legal_hub_task_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    author_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LegalHubTaskFileModel(Base):
    __tablename__ = "legal_hub_task_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LegalHubTaskEventModel(Base):
    __tablename__ = "legal_hub_task_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    text: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    sidebar_order: Mapped[list | None] = mapped_column(JSON, nullable=True)
    archived_clients: Mapped[list | None] = mapped_column(JSON, nullable=True)
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
