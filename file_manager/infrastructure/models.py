from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base


class MatterWorkspaceModel(Base):
    """Matter workspace (Clients → Matters come from time tracking)."""

    __tablename__ = "fm_matters"

    matter_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(80), nullable=False, default="", index=True)
    provisioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provisioned_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)


class FolderModel(Base):
    __tablename__ = "fm_folders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    trash_root_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class DocumentModel(Base):
    __tablename__ = "fm_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    doc_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(80), nullable=False, default="", index=True)
    folder_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_out_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    checked_out_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    trash_root_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class VersionModel(Base):
    __tablename__ = "fm_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    comment: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AclModel(Base):
    """Explicit matter permission; overrides access inherited from time tracking."""

    __tablename__ = "fm_acl"

    matter_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    granted_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccessRequestModel(Base):
    __tablename__ = "fm_access_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    matter_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    client_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="read")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    decided_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditModel(Base):
    __tablename__ = "fm_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, default="", index=True)
    matter_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(80), nullable=False, default="", index=True)
    target_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")


class FavoriteModel(Base):
    __tablename__ = "fm_favorites"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SettingModel(Base):
    __tablename__ = "fm_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
