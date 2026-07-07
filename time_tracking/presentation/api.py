from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from application.client_expense_category_defaults import (
    seed_default_expense_categories_for_all_clients,
)
from application.client_task_defaults import seed_default_tasks_for_all_projects_missing_tasks
from infrastructure.database import Base, async_session_factory, engine
from infrastructure import models
from infrastructure import models_reports
from infrastructure import models_invoices
from infrastructure.schema_patches import (
    apply_client_expense_categories_schema_patch,
    apply_client_projects_schema_patch,
    apply_client_tasks_project_scope_migration,
    apply_client_tasks_schema_patch,
    apply_team_workload_schema_patch,
    apply_client_extra_contacts_schema_patch,
    apply_time_manager_clients_schema_patch,
    apply_user_project_access_patch,
    apply_time_entries_task_id_schema_patch,
    apply_time_entries_hours_precision_patch,
    apply_time_entries_seconds_and_rounded_patch,
    apply_time_entries_external_reference_patch,
    apply_time_entries_manager_void_patch,
    apply_reports_schema_patch,
    apply_invoices_schema_patch,
    apply_project_currency_patch,
    apply_weekly_submissions_schema_patch,
    apply_client_projects_project_billable_amount_patch,
    apply_client_projects_records_language_patch,
    apply_hourly_rates_applies_to_project_patch,
    apply_project_scoped_billable_rates_open_interval_patch,
    apply_time_tracking_teams_schema_patch,
    apply_report_performance_indexes_patch,
    apply_time_entry_archives_patch,
)
from application.settings_sync import renormalize_time_entries_to_minute
from presentation.deps import require_bearer_user, require_tt_reports_viewer
from presentation.exception_handlers import register_exception_handlers
from presentation.routes import (
    labor_statistics,
    invoices,
    client_contacts,
    client_expense_categories,
    client_projects,
    client_tasks,
    clients,
    health,
    hourly_rates,
    project_access,
    report_partner_confirmations,
    report_snapshots,
    reports,
    team_workload,
    teams,
    time_entries,
    users,
    weekly_submissions,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await apply_team_workload_schema_patch(conn)
        await apply_time_manager_clients_schema_patch(conn)
        await apply_client_extra_contacts_schema_patch(conn)
        await apply_client_expense_categories_schema_patch(conn)
        await apply_client_projects_schema_patch(conn)
        await apply_client_tasks_schema_patch(conn)
        await apply_client_tasks_project_scope_migration(conn)
        await apply_user_project_access_patch(conn)
        await apply_time_entries_task_id_schema_patch(conn)
        await apply_time_entries_hours_precision_patch(conn)
        await apply_reports_schema_patch(conn)
        await apply_invoices_schema_patch(conn)
        await apply_project_currency_patch(conn)
        await apply_time_entries_seconds_and_rounded_patch(conn)
        await apply_time_entries_external_reference_patch(conn)
        await apply_time_entries_manager_void_patch(conn)
        await apply_weekly_submissions_schema_patch(conn)
        await apply_client_projects_project_billable_amount_patch(conn)
        await apply_client_projects_records_language_patch(conn)
        await apply_hourly_rates_applies_to_project_patch(conn)
        await apply_project_scoped_billable_rates_open_interval_patch(conn)
        await apply_time_tracking_teams_schema_patch(conn)
        await apply_report_performance_indexes_patch(conn)
        await apply_time_entry_archives_patch(conn)
    async with async_session_factory() as session:
        await seed_default_tasks_for_all_projects_missing_tasks(session)
        await seed_default_expense_categories_for_all_clients(session)

        await renormalize_time_entries_to_minute(session)
        await session.commit()
    yield


app = FastAPI(
    title="Time Tracking",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
register_exception_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)

_tt_auth = [Depends(require_bearer_user)]
_tt_reports_auth = [Depends(require_bearer_user), Depends(require_tt_reports_viewer)]

app.include_router(health.router)
app.include_router(client_tasks.router, dependencies=_tt_auth)
app.include_router(client_expense_categories.router, dependencies=_tt_auth)
app.include_router(client_projects.router, dependencies=_tt_auth)
app.include_router(client_contacts.router, dependencies=_tt_auth)
app.include_router(clients.router, dependencies=_tt_auth)
app.include_router(team_workload.router, dependencies=_tt_auth)
app.include_router(teams.router, dependencies=_tt_auth)
app.include_router(hourly_rates.router, dependencies=_tt_auth)
app.include_router(time_entries.router, dependencies=_tt_auth)
app.include_router(weekly_submissions.router, dependencies=_tt_auth)
app.include_router(project_access.router, dependencies=_tt_auth)
app.include_router(users.router, dependencies=_tt_auth)
app.include_router(reports.router, dependencies=_tt_reports_auth)
app.include_router(labor_statistics.router, dependencies=_tt_reports_auth)
app.include_router(report_partner_confirmations.router, dependencies=_tt_reports_auth)
app.include_router(report_snapshots.router, dependencies=_tt_reports_auth)
app.include_router(invoices.router, dependencies=_tt_auth)
app.include_router(client_projects._global_projects_router, dependencies=_tt_auth)
