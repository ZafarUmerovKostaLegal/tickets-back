

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database import Base


class ReportSavedViewModel(Base):


    __tablename__ = "tt_report_saved_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    filters_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportSnapshotModel(Base):


    __tablename__ = "tt_report_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    group_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filters_json: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rows: Mapped[list["ReportSnapshotRowModel"]] = relationship(
        "ReportSnapshotRowModel",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="ReportSnapshotRowModel.sort_order",
    )


class ReportSnapshotRowModel(Base):


    __tablename__ = "tt_report_snapshot_rows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tt_report_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_data_json: Mapped[str] = mapped_column(Text, nullable=False)
    overrides_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshot: Mapped["ReportSnapshotModel"] = relationship(
        "ReportSnapshotModel", back_populates="rows"
    )


class ReportPartnerConfirmationRequestModel(Base):


    __tablename__ = "tt_report_partner_confirmation_requests"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "project_id",
            "date_from",
            "date_to",
            name="uq_tt_report_partner_conf_snap_proj_period",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tt_report_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_client_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(700), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    review_priority: Mapped[str] = mapped_column(String(16), nullable=False, default="yellow")
    submitted_by_auth_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    signatures: Mapped[list["ReportPartnerConfirmationSignatureModel"]] = relationship(
        "ReportPartnerConfirmationSignatureModel",
        back_populates="request",
        cascade="all, delete-orphan",
    )
    comments: Mapped[list["ReportPartnerConfirmationCommentModel"]] = relationship(
        "ReportPartnerConfirmationCommentModel",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ReportPartnerConfirmationCommentModel.created_at",
    )


class ReportPartnerConfirmationSignatureModel(Base):


    __tablename__ = "tt_report_partner_confirmation_signatures"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "partner_auth_user_id",
            name="uq_tt_report_partner_conf_sig",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tt_report_partner_confirmation_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    partner_auth_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request: Mapped["ReportPartnerConfirmationRequestModel"] = relationship(
        "ReportPartnerConfirmationRequestModel", back_populates="signatures"
    )


class ReportPartnerConfirmationCommentModel(Base):
    __tablename__ = "tt_report_partner_confirmation_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tt_report_partner_confirmation_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    auth_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    request: Mapped["ReportPartnerConfirmationRequestModel"] = relationship(
        "ReportPartnerConfirmationRequestModel", back_populates="comments"
    )
