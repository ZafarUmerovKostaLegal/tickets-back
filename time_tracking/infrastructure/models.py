

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database import Base


class TimeTrackingUserModel(Base):


    __tablename__ = "time_tracking_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    auth_user_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)

    position: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weekly_capacity_hours: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=Decimal("35"),
        server_default=text("35"),
    )

    reports_to_auth_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserHourlyRateModel(Base):


    __tablename__ = "time_tracking_user_hourly_rates"
    __table_args__ = (
        Index("ix_tt_hourly_rates_user_kind", "auth_user_id", "rate_kind"),
        Index("ix_tt_hourly_rates_project_scope", "applies_to_project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    auth_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("time_tracking_users.auth_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    rate_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    applies_to_project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("time_tracking_client_projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[date | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimeEntryModel(Base):


    __tablename__ = "time_tracking_entries"
    __table_args__ = (
        Index("ix_tt_entries_user_date", "auth_user_id", "work_date"),
        Index("ix_tt_entries_project_task", "project_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    auth_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("time_tracking_users.auth_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)


    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    hours: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False)

    rounded_hours: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False)
    is_billable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("time_tracking_client_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_reference_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by_auth_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    void_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimeManagerClientModel(Base):


    __tablename__ = "time_tracking_clients"
    __table_args__ = (Index("ix_tt_clients_name", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    invoice_due_mode: Mapped[str] = mapped_column(String(50), nullable=False, default="custom")
    invoice_due_days_after_issue: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tax_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    tax2_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    extra_contacts: Mapped[list["TimeManagerClientContactModel"]] = relationship(
        "TimeManagerClientContactModel",
        back_populates="client",
        cascade="all, delete-orphan",
    )


class TimeManagerClientContactModel(Base):


    __tablename__ = "time_tracking_client_contacts"
    __table_args__ = (Index("ix_tt_client_contacts_client", "client_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped["TimeManagerClientModel"] = relationship(
        "TimeManagerClientModel", back_populates="extra_contacts"
    )


class TimeManagerClientTaskModel(Base):


    __tablename__ = "time_tracking_client_tasks"
    __table_args__ = (Index("ix_tt_client_tasks_client", "client_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    default_billable_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    billable_by_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    common_for_future_projects: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    add_to_existing_projects: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimeManagerClientExpenseCategoryModel(Base):


    __tablename__ = "time_tracking_client_expense_categories"
    __table_args__ = (Index("ix_tt_client_exp_cat_client", "client_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    has_unit_price: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimeManagerClientProjectModel(Base):


    __tablename__ = "time_tracking_client_projects"
    __table_args__ = (Index("ix_tt_client_projects_client", "client_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="managers_only")
    project_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="time_and_materials",
    )
    currency: Mapped[str] = mapped_column(
        String(10), nullable=False, default="USD", server_default=text("'USD'")
    )
    billable_rate_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_billable_rate_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    budget_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    budget_hours: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    budget_resets_every_month: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    budget_includes_expenses: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    send_budget_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    budget_alert_threshold_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    fixed_fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimeTrackingUserProjectAccessModel(Base):


    __tablename__ = "time_tracking_user_project_access"
    __table_args__ = (
        UniqueConstraint("auth_user_id", "project_id", name="uq_tt_user_project_access"),
        Index("ix_tt_upa_user", "auth_user_id"),
        Index("ix_tt_upa_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    auth_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("time_tracking_users.auth_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("time_tracking_client_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    granted_by_auth_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WeeklyTimeSubmissionModel(Base):


    __tablename__ = "time_tracking_weekly_submissions"
    __table_args__ = (
        UniqueConstraint("auth_user_id", "week_start", name="uq_tt_weekly_sub_user_week"),
        Index("ix_tt_weekly_sub_user_dates", "auth_user_id", "week_start", "week_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    auth_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("time_tracking_users.auth_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    auto_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
