from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application.kind_legend import KIND_LEGEND_ENTRIES, KindLegendEntry
from application.schedule_employee_sync import SyncScheduleEmployeesResult, sync_schedule_employees_for_year
from infrastructure.auth_lookup import list_staff_users
from infrastructure.database import get_session
from infrastructure.models import AbsenceDay, ScheduleEmployee

router = APIRouter(prefix="/schedule", tags=["schedule"])


KIND_LABELS: dict[int, str] = {
    1: "annual_vacation",
    2: "sick_leave",
    3: "day_off",
    4: "business_trip",
    5: "remote_work",
}


class EmployeeOut(BaseModel):
    id: int
    year: int
    excel_row_no: int | None = None
    auth_user_id: int | None = Field(None, alias="authUserId")
    full_name: str
    email: str | None = None
    planned_period_note: str | None = None

    model_config = {"populate_by_name": True}


class AbsenceDayOut(BaseModel):
    id: int
    absence_on: date
    kind_code: int
    kind: str = Field(description="Стабильный ключ вида отсутствия")


class AbsenceDayWithEmployeeOut(BaseModel):
    id: int
    employee_id: int
    full_name: str
    absence_on: date
    kind_code: int
    kind: str


class EmployeeWithAbsencesOut(EmployeeOut):
    absence_days: list[AbsenceDayOut]


class ImportResultOut(BaseModel):
    year: int
    employees_imported: int
    absence_days_imported: int


class SyncEmployeesResultOut(BaseModel):
    year: int
    created: int = Field(..., alias="created")
    linked_orphans: int = Field(..., alias="linkedOrphans")
    updated: int
    skipped_archived: int = Field(..., alias="skippedArchived")
    skipped_hidden: int = Field(..., alias="skippedHidden")

    model_config = {"populate_by_name": True}


class EmployeeCreateBody(BaseModel):
    year: int = Field(..., ge=2000, le=2100)
    full_name: str = Field(..., min_length=1, max_length=500)
    auth_user_id: int | None = Field(None, alias="authUserId")
    email: str | None = Field(None, max_length=320)
    planned_period_note: str | None = None

    model_config = {"populate_by_name": True}


class EmployeePatchBody(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=500)
    planned_period_note: str | None = None
    auth_user_id: int | None = Field(None, alias="authUserId")
    email: str | None = Field(None, max_length=320)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def at_least_one(self):
        if (
            self.full_name is None
            and self.planned_period_note is None
            and "auth_user_id" not in self.model_fields_set
            and self.email is None
        ):
            raise ValueError("Укажите хотя бы одно поле для обновления")
        return self


class AbsenceDayCreateBody(BaseModel):
    absence_on: date
    kind_code: int = Field(..., ge=1, le=5)


class AbsenceDayPatchBody(BaseModel):
    absence_on: date | None = None
    kind_code: int | None = Field(None, ge=1, le=5)

    @model_validator(mode="after")
    def at_least_one(self):
        if self.absence_on is None and self.kind_code is None:
            raise ValueError("Укажите хотя бы одно поле для обновления")
        return self


def _absence_out(d: AbsenceDay) -> AbsenceDayOut:
    return AbsenceDayOut(
        id=d.id,
        absence_on=d.absence_on,
        kind_code=d.kind_code,
        kind=KIND_LABELS.get(d.kind_code, "unknown"),
    )


@router.post("/import", response_model=ImportResultOut)
async def import_excel_upload():
    raise HTTPException(
        status_code=410,
        detail=(
            "Импорт из Excel отключён. График заполняется автоматически: "
            "сотрудник подаёт заявку через POST /api/v1/vacations/leave-requests, "
            "после approve дни появляются в schedule_employees/absence_days."
        ),
    )


@router.get("/kind-codes", response_model=dict[str, str])
async def kind_codes() -> dict[str, str]:

    return {str(k): v for k, v in KIND_LABELS.items()}


@router.get("/kind-legend", response_model=list[KindLegendEntry])
async def kind_legend() -> list[KindLegendEntry]:

    return list(KIND_LEGEND_ENTRIES)


@router.post("/employees/sync", response_model=SyncEmployeesResultOut)
async def sync_employees_from_auth(
    year: int = Query(..., ge=2000, le=2100),
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    """Привязать всех auth-пользователей к графику на год (активировать строки)."""
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    staff = await list_staff_users(authorization)
    try:
        result = await sync_schedule_employees_for_year(session, year=year, staff_users=staff)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Конфликт при привязке сотрудников к графику (дубликат auth_user_id или ФИО)",
        ) from None
    return SyncEmployeesResultOut(
        year=result.year,
        created=result.created,
        linked_orphans=result.linked_orphans,
        updated=result.updated,
        skipped_archived=result.skipped_archived,
        skipped_hidden=result.skipped_hidden,
    )


@router.get("/employees", response_model=list[EmployeeOut])
async def list_employees(
    year: int = Query(..., ge=2000, le=2100),
    only_registered: bool = Query(
        True,
        description="Возвращать только сотрудников, привязанных к зарегистрированному auth-пользователю",
    ),
    session: AsyncSession = Depends(get_session),
):
    q = select(ScheduleEmployee).where(ScheduleEmployee.year == year)
    if only_registered:
        q = q.where(ScheduleEmployee.auth_user_id.is_not(None))
    q = q.order_by(ScheduleEmployee.full_name.asc(), ScheduleEmployee.id)
    r = await session.execute(q)
    rows = r.scalars().all()
    return [
        EmployeeOut(
            id=e.id,
            year=e.year,
            excel_row_no=e.excel_row_no,
            auth_user_id=e.auth_user_id,
            full_name=e.full_name,
            email=e.email,
            planned_period_note=e.planned_period_note,
        )
        for e in rows
    ]


@router.get("/employees/{employee_id}", response_model=EmployeeWithAbsencesOut)
async def get_employee(
    employee_id: int,
    year: int | None = Query(None, ge=2000, le=2100),
    session: AsyncSession = Depends(get_session),
):
    q = (
        select(ScheduleEmployee)
        .options(selectinload(ScheduleEmployee.absence_days))
        .where(ScheduleEmployee.id == employee_id)
    )
    r = await session.execute(q)
    e = r.scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    if year is not None and e.year != year:
        raise HTTPException(status_code=404, detail="Employee not found for this year")
    days = sorted(e.absence_days, key=lambda d: d.absence_on)
    return EmployeeWithAbsencesOut(
        id=e.id,
        year=e.year,
        excel_row_no=e.excel_row_no,
        auth_user_id=e.auth_user_id,
        full_name=e.full_name,
        email=e.email,
        planned_period_note=e.planned_period_note,
        absence_days=[_absence_out(d) for d in days],
    )


@router.get("/absence-days", response_model=list[AbsenceDayWithEmployeeOut])
async def list_absence_days(
    year: int = Query(..., ge=2000, le=2100),
    employee_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    session: AsyncSession = Depends(get_session),
):
    q = select(AbsenceDay, ScheduleEmployee).join(ScheduleEmployee).where(ScheduleEmployee.year == year)
    if employee_id is not None:
        q = q.where(AbsenceDay.employee_id == employee_id)
    if date_from is not None:
        q = q.where(AbsenceDay.absence_on >= date_from)
    if date_to is not None:
        q = q.where(AbsenceDay.absence_on <= date_to)
    q = q.order_by(AbsenceDay.absence_on, AbsenceDay.employee_id)
    r = await session.execute(q)
    out: list[AbsenceDayWithEmployeeOut] = []
    for d, emp in r.all():
        out.append(
            AbsenceDayWithEmployeeOut(
                id=d.id,
                employee_id=emp.id,
                full_name=emp.full_name,
                absence_on=d.absence_on,
                kind_code=d.kind_code,
                kind=KIND_LABELS.get(d.kind_code, "unknown"),
            )
        )
    return out


@router.post("/employees", response_model=EmployeeOut)
async def create_employee(
    body: EmployeeCreateBody,
    session: AsyncSession = Depends(get_session),
):
    emp = ScheduleEmployee(
        year=body.year,
        excel_row_no=None,
        auth_user_id=body.auth_user_id,
        full_name=body.full_name.strip(),
        email=(body.email.strip() if body.email and body.email.strip() else None),
        planned_period_note=(body.planned_period_note.strip() if body.planned_period_note and body.planned_period_note.strip() else None),
    )
    session.add(emp)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Конфликт уникальности записи") from None
    await session.refresh(emp)
    return EmployeeOut(
        id=emp.id,
        year=emp.year,
        excel_row_no=emp.excel_row_no,
        auth_user_id=emp.auth_user_id,
        full_name=emp.full_name,
        email=emp.email,
        planned_period_note=emp.planned_period_note,
    )


@router.patch("/employees/{employee_id}", response_model=EmployeeOut)
async def patch_employee(
    employee_id: int,
    body: EmployeePatchBody,
    session: AsyncSession = Depends(get_session),
):
    r = await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.id == employee_id))
    emp = r.scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    if body.full_name is not None:
        emp.full_name = body.full_name.strip()
    if body.planned_period_note is not None:
        emp.planned_period_note = body.planned_period_note.strip() if body.planned_period_note.strip() else None
    if "auth_user_id" in body.model_fields_set:
        emp.auth_user_id = body.auth_user_id
    if body.email is not None:
        emp.email = body.email.strip() if body.email.strip() else None
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Конфликт уникальности записи") from None
    await session.refresh(emp)
    return EmployeeOut(
        id=emp.id,
        year=emp.year,
        excel_row_no=emp.excel_row_no,
        auth_user_id=emp.auth_user_id,
        full_name=emp.full_name,
        email=emp.email,
        planned_period_note=emp.planned_period_note,
    )


@router.delete("/employees/{employee_id}", status_code=204)
async def delete_employee(
    employee_id: int,
    session: AsyncSession = Depends(get_session),
):
    r = await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.id == employee_id))
    emp = r.scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    await session.delete(emp)
    await session.commit()


@router.post("/employees/{employee_id}/absence-days", response_model=AbsenceDayOut)
async def create_absence_day(
    employee_id: int,
    body: AbsenceDayCreateBody,
    session: AsyncSession = Depends(get_session),
):
    r = await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.id == employee_id))
    emp = r.scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    if body.absence_on.year != emp.year:
        raise HTTPException(
            status_code=400,
            detail=f"Дата должна быть в году графика сотрудника ({emp.year})",
        )
    row = AbsenceDay(employee_id=employee_id, absence_on=body.absence_on, kind_code=body.kind_code)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="На эту дату для сотрудника уже есть отметка",
        ) from None
    await session.refresh(row)
    return _absence_out(row)


@router.patch("/absence-days/{absence_day_id}", response_model=AbsenceDayOut)
async def patch_absence_day(
    absence_day_id: int,
    body: AbsenceDayPatchBody,
    session: AsyncSession = Depends(get_session),
):
    r = await session.execute(
        select(AbsenceDay).where(AbsenceDay.id == absence_day_id),
    )
    row = r.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Absence day not found")
    er = await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.id == row.employee_id))
    emp = er.scalar_one()
    new_date = body.absence_on if body.absence_on is not None else row.absence_on
    if new_date.year != emp.year:
        raise HTTPException(
            status_code=400,
            detail=f"Дата должна быть в году графика сотрудника ({emp.year})",
        )
    if body.absence_on is not None:
        row.absence_on = body.absence_on
    if body.kind_code is not None:
        row.kind_code = body.kind_code
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="На эту дату для сотрудника уже есть другая отметка",
        ) from None
    await session.refresh(row)
    return _absence_out(row)


@router.delete("/absence-days/{absence_day_id}", status_code=204)
async def delete_absence_day(
    absence_day_id: int,
    session: AsyncSession = Depends(get_session),
):
    r = await session.execute(select(AbsenceDay).where(AbsenceDay.id == absence_day_id))
    row = r.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Absence day not found")
    await session.delete(row)
    await session.commit()
