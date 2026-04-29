

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.repositories import ExpenseRepository
from presentation.deps import check_view_role, get_current_user
from presentation.schemas import DepartmentRefOut, ExchangeRateOut, ExpenseTypeRefOut, ProjectRefOut

router = APIRouter(tags=["expenses-reference"])


@router.get("/expense-types", response_model=list[ExpenseTypeRefOut])
async def list_expense_types(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    rows = await repo.list_expense_types()
    return [ExpenseTypeRefOut(code=r.code, label=r.label, sort_order=r.sort_order) for r in rows]


@router.get("/departments", response_model=list[DepartmentRefOut])
async def list_departments(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    rows = await repo.list_departments()
    return [DepartmentRefOut(id=r.id, name=r.name) for r in rows]


@router.get("/projects", response_model=list[ProjectRefOut])
async def list_projects(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    rows = await repo.list_projects()
    return [ProjectRefOut(id=r.id, name=r.name) for r in rows]


@router.get("/expenses/project-totals/{project_id}")
async def get_project_expense_totals(
    project_id: str,
    date_from: date | None = Query(None, alias="dateFrom"),
    date_to: date | None = Query(None, alias="dateTo"),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):

    check_view_role(user)
    repo = ExpenseRepository(session)
    return await repo.aggregate_expenses_for_project(project_id, date_from, date_to)


@router.get("/expenses/report-data")
async def get_expense_report_data(
    dateFrom: str = Query(..., alias="dateFrom"),
    dateTo: str = Query(..., alias="dateTo"),
    userIds: Optional[str] = Query(None, alias="userIds"),
    projectIds: Optional[str] = Query(None, alias="projectIds"),
    session: AsyncSession = Depends(get_session),
):

    try:
        df = date.fromisoformat(dateFrom.strip()[:10])
        dt = date.fromisoformat(dateTo.strip()[:10])
    except (ValueError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid date: {e}")

    uid_list: list[int] | None = None
    if userIds:
        uid_list = [int(x.strip()) for x in userIds.split(",") if x.strip().isdigit()]

    pid_list: list[str] | None = None
    if projectIds:
        pid_list = [x.strip() for x in projectIds.split(",") if x.strip()]

    repo = ExpenseRepository(session)
    rows = await repo.list_for_report(
        date_from=df,
        date_to=dt,
        user_ids=uid_list or None,
        project_ids=pid_list or None,
    )
    return [
        {
            "id": r.id,
            "expense_date": r.expense_date.isoformat() if r.expense_date else None,
            "project_id": r.project_id,
            "expense_category_id": r.expense_category_id,
            "amount_uzs": float(r.amount_uzs),
            "exchange_rate": float(r.exchange_rate),
            "equivalent_amount": float(r.equivalent_amount),
            "expense_type": r.expense_type,
            "status": r.status,
            "created_by_user_id": r.created_by_user_id,
            "description": r.description or "",
            "is_reimbursable": r.is_reimbursable,
        }
        for r in rows
    ]


@router.get("/exchange-rates", response_model=ExchangeRateOut)
async def get_exchange_rate(
    date_param: date = Query(..., alias="date"),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_exchange_rate_for_date(date_param)
    if not row:
        raise HTTPException(status_code=404, detail="Курс на указанную дату не найден")
    return ExchangeRateOut(date=row.rate_date, rate=row.rate, pair_label=row.pair_label)
