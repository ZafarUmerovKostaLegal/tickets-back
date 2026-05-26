import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.database import Base, engine
from infrastructure import models
from infrastructure.schema_readiness import mark_schema_ready
from presentation.middleware.schema_readiness import SchemaReadinessMiddleware
from presentation.routes.health import router as health_router
from presentation.routes.schedule import router as schedule_router
from presentation.routes.leave_requests import router as leave_requests_router

_log = logging.getLogger("vacation.startup")
_STARTUP_RETRIES = 30
_STARTUP_DELAY_SEC = 2.0


def _is_database_missing_error(exc: BaseException) -> bool:
    if type(exc).__name__ == "InvalidCatalogNameError":
        return True
    s = str(exc).lower()
    return "does not exist" in s and "database" in s


_UPGRADE_SQL = (
    # schedule_employees columns added in 2026
    "ALTER TABLE schedule_employees ADD COLUMN IF NOT EXISTS auth_user_id INTEGER",
    "ALTER TABLE schedule_employees ADD COLUMN IF NOT EXISTS email VARCHAR(320)",
    "ALTER TABLE schedule_employees ALTER COLUMN excel_row_no DROP NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_schedule_employees_auth_user_id ON schedule_employees(auth_user_id)",
    """DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_schedule_employees_year_auth_user'
        ) THEN
            ALTER TABLE schedule_employees
                ADD CONSTRAINT uq_schedule_employees_year_auth_user UNIQUE (year, auth_user_id);
        END IF;
    END $$""",
    "ALTER TABLE absence_days ADD COLUMN IF NOT EXISTS leave_request_id INTEGER",
    "CREATE INDEX IF NOT EXISTS ix_absence_days_leave_request_id ON absence_days(leave_request_id)",
)


async def _apply_upgrade_sql() -> None:
    if engine is None:
        return
    from sqlalchemy import text

    async with engine.begin() as conn:
        for stmt in _UPGRADE_SQL:
            try:
                await conn.execute(text(stmt))
            except Exception as exc:  # pragma: no cover - defensive
                _log.warning("upgrade step failed (%s): %s", stmt[:80], exc)


async def _ensure_schema_with_retries() -> None:

    last_exc: Exception | None = None
    for attempt in range(1, _STARTUP_RETRIES + 1):
        try:
            if engine is None:
                return
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await _apply_upgrade_sql()
            mark_schema_ready()
            _log.info("Схема БД vacation готова")
            return
        except Exception as e:
            last_exc = e
            if attempt == 1 and _is_database_missing_error(e):
                _log.error(
                    "База с именем из VACATION_DB_NAME в Postgres не найдена. Создайте её "
                    '(CREATE DATABASE "имя"; под суперпользователем) или задайте VACATION_DB_NAME '
                    "и у контейнера vacation_db тот же POSTGRES_DB, что и у реально существующей БД. "
                    "Частая ошибка: в томе уже создана kosta_vacation, а в .env указано kosta-vacation.",
                )
            _log.warning(
                "БД недоступна для vacation (попытка %s/%s): %s",
                attempt,
                _STARTUP_RETRIES,
                e,
            )
            await asyncio.sleep(_STARTUP_DELAY_SEC)
    assert last_exc is not None
    _log.error("Не удалось подключиться к БД vacation после %s попыток", _STARTUP_RETRIES)
    raise last_exc


def _schema_task_done(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        _log.error("Инициализация схемы vacation не удалась", exc_info=e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine is None:
        yield
        return

    task = asyncio.create_task(_ensure_schema_with_retries())
    task.add_done_callback(_schema_task_done)
    try:
        yield
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="Kosta Vacation / absence schedule",
    version="0.1.0",
    description="График отсутствий (импорт из Excel).",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)

app.add_middleware(SchemaReadinessMiddleware)
app.include_router(health_router)
app.include_router(schedule_router)
app.include_router(leave_requests_router)
