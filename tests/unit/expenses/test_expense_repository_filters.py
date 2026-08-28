from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from infrastructure.repositories import ExpenseRepository


def _result_rows():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


def _result_count():
    result = MagicMock()
    result.scalar.return_value = 0
    return result


def _result_sums():
    result = MagicMock()
    result.one.return_value = (Decimal("0"), Decimal("0"))
    return result


@pytest.mark.asyncio
async def test_company_scope_keeps_selected_expense_type_filter():
    session = AsyncMock()
    session.execute.side_effect = [_result_rows(), _result_count(), _result_sums()]

    await ExpenseRepository(session).list_requests(
        created_by_user_id=None,
        status=None,
        scope=None,
        expense_type="transport",
        is_reimbursable=None,
        date_from=None,
        date_to=None,
        department_id=None,
        project_id=None,
        search=None,
        sort_by="createdAt",
        sort_order="desc",
        skip=0,
        limit=50,
        scope_mode="company",
    )

    query = session.execute.await_args_list[0].args[0]
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "expense_requests.expense_type != 'partner_expense'" in sql
    assert "expense_requests.expense_type = 'transport'" in sql


@pytest.mark.asyncio
async def test_registry_scope_keeps_selected_status_filter():
    session = AsyncMock()
    session.execute.side_effect = [_result_rows(), _result_count(), _result_sums()]

    await ExpenseRepository(session).list_requests(
        created_by_user_id=None,
        status="approved",
        scope="registry",
        expense_type=None,
        is_reimbursable=None,
        date_from=None,
        date_to=None,
        department_id=None,
        project_id=None,
        search=None,
        sort_by="createdAt",
        sort_order="desc",
        skip=0,
        limit=50,
    )

    query = session.execute.await_args_list[0].args[0]
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "expense_requests.status IN (" in sql
    assert "expense_requests.status = 'approved'" in sql


@pytest.mark.asyncio
async def test_awaiting_payment_excludes_employee_cash_payouts():
    session = AsyncMock()
    session.execute.side_effect = [_result_rows(), _result_count(), _result_sums()]

    await ExpenseRepository(session).list_requests(
        created_by_user_id=None,
        status="approved",
        scope=None,
        expense_type=None,
        is_reimbursable=True,
        date_from=None,
        date_to=None,
        department_id=None,
        project_id=None,
        search=None,
        sort_by="createdAt",
        sort_order="desc",
        skip=0,
        limit=50,
        awaiting_payment=True,
    )

    query = session.execute.await_args_list[0].args[0]
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "expense_requests.expense_type = 'partner_expense'" in sql
    assert "expense_requests.payment_method IS NULL" in sql
    assert "lower(expense_requests.payment_method) != 'cash'" in sql
