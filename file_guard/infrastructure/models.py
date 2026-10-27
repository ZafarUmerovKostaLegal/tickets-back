from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base


class FileGuardEventModel(Base):
    __tablename__ = "file_guard_events"
    __table_args__ = (UniqueConstraint("client_event_id", name="uq_file_guard_client_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_event_id: Mapped[str] = mapped_column(String(80), nullable=False)
    host: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    windows_user: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    process_name: Mapped[str] = mapped_column(String(260), nullable=False, default="")
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    file_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file_name: Mapped[str] = mapped_column(String(260), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="send")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="blocked")
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stored_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FileGuardPolicyModel(Base):
    __tablename__ = "file_guard_policies"
    __table_args__ = (UniqueConstraint("host", "windows_user", name="uq_file_guard_policy_scope"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    host: Mapped[str] = mapped_column(String(120), nullable=False, default="*")
    windows_user: Mapped[str] = mapped_column(String(200), nullable=False, default="*")
    daily_send_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
