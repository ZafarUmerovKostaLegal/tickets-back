from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column
from infrastructure.database import Base


class WorkdaySettingsModel(Base):
    __tablename__ = "attendance_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workday_start: Mapped[time] = mapped_column(Time, nullable=False)
    workday_end: Mapped[time] = mapped_column(Time, nullable=False)
    late_threshold_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    daily_hours_norm: Mapped[int] = mapped_column(Integer, nullable=False, default=8)


class HikvisionUserBindingModel(Base):
    __tablename__ = "attendance_hikvision_user_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_employee_no: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    app_user_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    camera_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AttendanceExplanationModel(Base):
    __tablename__ = "attendance_explanations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    app_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    camera_employee_no: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    explanation_text: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    explanation_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
