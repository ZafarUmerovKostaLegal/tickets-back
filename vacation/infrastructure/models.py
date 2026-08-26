from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.orm_base import Base


LEAVE_STATUS_PENDING = "pending"
# Курирующий партнёр согласовал, ждём финального решения управляющего партнёра.
LEAVE_STATUS_PENDING_FINAL = "pending_final"
LEAVE_STATUS_APPROVED = "approved"
LEAVE_STATUS_DECLINED = "declined"
LEAVE_STATUS_CANCELLED = "cancelled"


class ScheduleEmployee(Base):


    __tablename__ = "schedule_employees"
    __table_args__ = (
        UniqueConstraint("year", "excel_row_no", name="uq_schedule_employees_year_row"),
        UniqueConstraint("year", "auth_user_id", name="uq_schedule_employees_year_auth_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    excel_row_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auth_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    full_name: Mapped[str] = mapped_column(String(500), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    planned_period_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    absence_days: Mapped[list["AbsenceDay"]] = relationship(
        "AbsenceDay",
        back_populates="employee",
        cascade="all, delete-orphan",
    )


class AbsenceDay(Base):


    __tablename__ = "absence_days"
    __table_args__ = (UniqueConstraint("employee_id", "absence_on", name="uq_absence_employee_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("schedule_employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    absence_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    leave_request_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("leave_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    manual_entry_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("manual_absence_entries.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    employee: Mapped["ScheduleEmployee"] = relationship("ScheduleEmployee", back_populates="absence_days")


class LeaveRequest(Base):


    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    employee_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    employee_full_name: Mapped[str] = mapped_column(String(500), nullable=False)
    employee_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    employee_position: Mapped[str | None] = mapped_column(String(300), nullable=True)

    partner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    partner_full_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    partner_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    kind_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    date_to: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    days_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=LEAVE_STATUS_PENDING, index=True)
    # Решение курирующего партнёра (первая ступень).
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Решение управляющего партнёра (вторая, обязательная ступень).
    final_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_decided_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    pdf_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Версия шаблона заявления, по которой собран сохранённый PDF.
    pdf_doc_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ManualAbsenceEntry(Base):
    """Ручная запись в график (вносит администратор/партнёр/офис-менеджер).

    Для каждой ручной записи обязательны документы-основания (см. AbsenceDocument),
    например приказ о командировке на период 5–10 числа. Из записи материализуются
    дни в absence_days (с привязкой manual_entry_id).
    """

    __tablename__ = "manual_absence_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("schedule_employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    date_to: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list["AbsenceDocument"]] = relationship(
        "AbsenceDocument",
        back_populates="manual_entry",
        cascade="all, delete-orphan",
    )


class AbsenceDocument(Base):
    """Документ-основание для ручной записи в график (или вложение к заявке)."""

    __tablename__ = "absence_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manual_entry_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("manual_absence_entries.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    leave_request_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("leave_requests.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    manual_entry: Mapped["ManualAbsenceEntry | None"] = relationship(
        "ManualAbsenceEntry",
        back_populates="documents",
    )
