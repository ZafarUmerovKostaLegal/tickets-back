from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.orm_base import Base


class ScheduleEmployee(Base):


    __tablename__ = "schedule_employees"
    __table_args__ = (UniqueConstraint("year", "excel_row_no", name="uq_schedule_employees_year_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    excel_row_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    full_name: Mapped[str] = mapped_column(String(500), nullable=False)
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

    employee: Mapped["ScheduleEmployee"] = relationship("ScheduleEmployee", back_populates="absence_days")
