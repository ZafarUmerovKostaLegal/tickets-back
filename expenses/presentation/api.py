import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend_common.schema_patch_runner import apply_registered_schema_patches
from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from backend_common.cors_origins import resolve_cors_origins
from infrastructure.database import Base, async_session_factory, engine
from infrastructure import models
from infrastructure.repositories import ExpenseRepository, seed_reference_data
from infrastructure.schema_patches import REGISTERED_EXPENSE_SCHEMA_PATCHES
from presentation.routes import cash, expense_email_action, expenses, health, reference, reimbursed_feed

_log = logging.getLogger("expenses.startup")


_STARTUP_RETRIES = 30
_STARTUP_DELAY_SEC = 2.0

_LEGACY_INT_PK = frozenset({"integer", "bigint", "smallint"})


async def _drop_legacy_integer_expense_tables(conn) -> None:

    result = await conn.execute(
        text(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'expense_requests'
              AND column_name = 'id'
            """
        )
    )
    row = result.first()
    if row is None:
        return
    dt = (row[0] or "").lower()
    if dt not in _LEGACY_INT_PK:
        return
    # Never drop data: the legacy tables move intact (rows, indexes, FKs, sequences) into a
    # timestamped backup schema, and fresh VARCHAR-id tables are created in "public".
    from datetime import datetime, timezone

    backup_schema = "expenses_legacy_backup_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    _log.warning(
        "Обнаружен legacy expense_requests.id (%s). Старые таблицы расходов перенесены без удаления данных "
        "в схему %s; в public создаются новые таблицы под VARCHAR id.",
        row[0],
        backup_schema,
    )
    # Identifiers are constants plus a digits-only timestamp — nothing user-controlled.
    create_schema_ddl = 'CREATE SCHEMA IF NOT EXISTS "' + backup_schema + '"'
    await conn.execute(text(create_schema_ddl))
    for table in (
        "expense_attachments",
        "expense_status_history",
        "expense_audit_logs",
        "expense_requests",
        "expense_kl_sequence",
    ):
        move_ddl = 'ALTER TABLE IF EXISTS public."' + table + '" SET SCHEMA "' + backup_schema + '"'
        await conn.execute(text(move_ddl))


@asynccontextmanager
async def lifespan(app: FastAPI):
    last_exc: Exception | None = None
    for attempt in range(1, _STARTUP_RETRIES + 1):
        try:
            async with engine.begin() as conn:
                await _drop_legacy_integer_expense_tables(conn)
                await conn.run_sync(Base.metadata.create_all)
                await apply_registered_schema_patches(
                    conn,
                    REGISTERED_EXPENSE_SCHEMA_PATCHES,
                    table_name="expenses_schema_patch_log",
                    log_prefix="expenses",
                )
            async with async_session_factory() as session:
                await seed_reference_data(session)
                await session.commit()
                repo = ExpenseRepository(session)
                paid, restored = await expenses._auto_pay_open_expenses(repo, session, None)
                if paid or restored:
                    _log.info(
                        "Заявки без согласования оплачены: %s; расходы за клиента возвращены на согласование: %s",
                        paid,
                        restored,
                    )
            break
        except Exception as e:
            last_exc = e
            _log.warning(
                "БД недоступна для инициализации (попытка %s/%s): %s",
                attempt,
                _STARTUP_RETRIES,
                e,
            )
            await asyncio.sleep(_STARTUP_DELAY_SEC)
    else:
        _log.error("Не удалось подключиться к БД после %s попыток", _STARTUP_RETRIES)
        assert last_exc is not None
        raise last_exc
    yield


app = FastAPI(
    title="Kosta Expenses",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=resolve_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.include_router(health.router)
                                                                                                           
                                                                                          
app.include_router(reference.router)
app.include_router(reimbursed_feed.router)
app.include_router(cash.router)
app.include_router(expenses.router)
app.include_router(expense_email_action.router)
