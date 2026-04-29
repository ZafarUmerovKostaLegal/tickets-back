

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from infrastructure.config import get_settings
from infrastructure.expense_db_reset import reset_expenses_database_schema
from presentation.deps import check_main_admin, get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])
_log = logging.getLogger(__name__)

_RESET_CONFIRM = "RESET_EXPENSES_DB"


class ResetExpensesDatabaseBody(BaseModel):
    confirm: str = Field(
        ...,
        description=f'Должно быть ровно "{_RESET_CONFIRM}"',
    )


class ResetExpensesDatabaseOut(BaseModel):
    ok: bool = True
    message: str = "База данных модуля расходов пересоздана, справочники заполнены заново."


@router.post("/expenses-database/reset", response_model=ResetExpensesDatabaseOut)
async def reset_expenses_database(
    body: ResetExpensesDatabaseBody,
    user: dict = Depends(get_current_user),
):

    check_main_admin(user)
    settings = get_settings()
    if not settings.expense_allow_database_reset:
        raise HTTPException(
            status_code=409,
            detail="Сброс БД отключён (EXPENSE_ALLOW_DATABASE_RESET=false).",
        )
    if (body.confirm or "").strip() != _RESET_CONFIRM:
        raise HTTPException(
            status_code=400,
            detail=f'Укажите confirm: "{_RESET_CONFIRM}"',
        )

    _log.warning(
        "expenses DB reset requested by user_id=%s email=%s",
        user.get("id"),
        user.get("email"),
    )
    await reset_expenses_database_schema()
    return ResetExpensesDatabaseOut()
