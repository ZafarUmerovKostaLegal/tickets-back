

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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
