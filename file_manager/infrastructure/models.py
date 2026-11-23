from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, LargeBinary, String, Text
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
    # «Удалить навсегда» only hides the item from the trash: rows and files are kept forever.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
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
    # «Удалить навсегда» only hides the item from the trash: rows and files are kept forever.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VersionModel(Base):
    __tablename__ = "fm_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Legacy versions keep bytes here; new ones store b"" and point to the on-disk blob (infrastructure/blobs.py).
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    blob_sha: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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


class EmailFilingModel(Base):
    """Where an Outlook message (or its attachment / whole thread) was filed — for "already filed" and suggestions."""

    __tablename__ = "fm_email_filings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(500), nullable=False, default="", index=True)
    conversation_id: Mapped[str] = mapped_column(String(300), nullable=False, default="", index=True)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False, default="", index=True)
    from_domain: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    folder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    filed_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    filed_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class DocumentFingerprintModel(Base):
    """Hash (and e-mail Message-ID) of a document's current version: finds duplicates and unchanged re-uploads."""

    __tablename__ = "fm_doc_fingerprints"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(500), nullable=False, default="", index=True)


class UploadSessionModel(Base):
    """A large file arriving in chunks (resumable). Removed when completed, cancelled or abandoned."""

    __tablename__ = "fm_upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    folder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    comment: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    allow_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AddinClientModel(Base):
    """One Kosta File add-in install (an Office app on a device) and who uses it."""

    __tablename__ = "fm_addin_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    host: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    office_version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    addin_version: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    auth_method: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    sso_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    sso_error: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class MailRuleModel(Base):
    """«Mail from X → matter / folder». Personal rules act on their owner's mailbox,
    shared rules (partners, admins) on every mailbox with auto-filing turned on."""

    __tablename__ = "fm_mail_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="personal", index=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    owner_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    match: Mapped[str] = mapped_column(String(320), nullable=False)
    matter_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    matter_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    matter_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    client_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    client_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    folder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    folder_path: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MailboxModel(Base):
    """A mailbox the server reads for auto-filing — only after its owner turned it on."""

    __tablename__ = "fm_mailboxes"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    user_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Only mail that arrives after this moment is filed — the old mailbox is never scanned.
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inbox_delta: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sent_delta: Mapped[str] = mapped_column(Text, nullable=False, default="")
    filed_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingLinkModel(Base):
    """A Teams or Zoom meeting whose recordings (and transcripts) go into a matter folder.

    One link per meeting (Teams: meeting chat id; Zoom: "zoom:<meeting occurrence UUID>");
    the person who linked it files the recordings in their name."""

    __tablename__ = "fm_meeting_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(10), nullable=False, default="teams")
    chat_id: Mapped[str] = mapped_column(String(300), nullable=False, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    user_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    topic: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    organizer_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    meeting_id: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    matter_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    matter_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    matter_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    client_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    client_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    folder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    folder_path: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    include_transcript: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_record: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Graph ids of recordings / transcripts already filed: each one goes into the matter once.
    filed_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    filed_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
