

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database import Base


class CorrespondenceDocumentModel(Base):
    __tablename__ = "correspondence_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    registry_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    counterparty: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    partner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    responsible_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attachments: Mapped[list["CorrespondenceAttachmentModel"]] = relationship(
        "CorrespondenceAttachmentModel",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class CorrespondenceAttachmentModel(Base):
    __tablename__ = "correspondence_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("correspondence_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    attachment_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    uploaded_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    document: Mapped["CorrespondenceDocumentModel"] = relationship(
        "CorrespondenceDocumentModel",
        back_populates="attachments",
    )


class CorrespondenceRegistryCounterModel(Base):
    __tablename__ = "correspondence_registry_counters"
    __table_args__ = (UniqueConstraint("direction", "year", name="uq_corr_registry_direction_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
