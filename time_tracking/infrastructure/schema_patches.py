

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from infrastructure.schema_patch_utils import add_columns_if_missing

_CLIENT_CONTACT_COLUMN_DEFINITIONS = (
    "phone VARCHAR(64)",
    "email VARCHAR(320)",
    "contact_name VARCHAR(500)",
    "contact_phone VARCHAR(64)",
    "contact_email VARCHAR(320)",
)

_PROJECT_BILLING_COLUMN_DEFINITIONS = (
    "project_type VARCHAR(32) NOT NULL DEFAULT 'time_and_materials'",
    "billable_rate_type VARCHAR(64)",
    "budget_type VARCHAR(64)",
    "budget_amount NUMERIC(18, 4)",
    "budget_hours NUMERIC(12, 2)",
    "budget_resets_every_month BOOLEAN NOT NULL DEFAULT FALSE",
    "budget_includes_expenses BOOLEAN NOT NULL DEFAULT FALSE",
    "send_budget_alerts BOOLEAN NOT NULL DEFAULT FALSE",
    "budget_alert_threshold_percent NUMERIC(8, 2)",
    "fixed_fee_amount NUMERIC(18, 4)",
)


async def apply_team_workload_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_users
                ADD COLUMN IF NOT EXISTS weekly_capacity_hours NUMERIC(10, 2) NOT NULL DEFAULT 35
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_users
                ADD COLUMN IF NOT EXISTS position VARCHAR(256)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_entries (
                id VARCHAR(36) PRIMARY KEY,
                auth_user_id INTEGER NOT NULL REFERENCES time_tracking_users (auth_user_id) ON DELETE CASCADE,
                work_date DATE NOT NULL,
                hours NUMERIC(12, 2) NOT NULL,
                is_billable BOOLEAN NOT NULL DEFAULT TRUE,
                project_id VARCHAR(36),
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entries_user_date
                ON time_tracking_entries (auth_user_id, work_date)
            """
        )
    )


async def apply_time_manager_clients_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_clients (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(500) NOT NULL,
                address TEXT,
                currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                invoice_due_mode VARCHAR(50) NOT NULL DEFAULT 'custom',
                invoice_due_days_after_issue INTEGER,
                tax_percent NUMERIC(8, 4),
                tax2_percent NUMERIC(8, 4),
                discount_percent NUMERIC(8, 4),
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_clients_name ON time_tracking_clients (name)
            """
        )
    )
    await apply_time_tracking_clients_contact_columns_patch(conn)
    await apply_time_tracking_clients_is_archived_patch(conn)


async def apply_time_tracking_clients_is_archived_patch(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_clients
                ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )


async def apply_time_tracking_clients_contact_columns_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_clients",
        _CLIENT_CONTACT_COLUMN_DEFINITIONS,
    )


async def apply_client_extra_contacts_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_client_contacts (
                id VARCHAR(36) PRIMARY KEY,
                client_id VARCHAR(36) NOT NULL REFERENCES time_tracking_clients (id) ON DELETE CASCADE,
                name VARCHAR(500) NOT NULL,
                phone VARCHAR(64),
                email VARCHAR(320),
                sort_order INTEGER,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_client_contacts_client
                ON time_tracking_client_contacts (client_id)
            """
        )
    )


async def apply_client_tasks_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_client_tasks (
                id VARCHAR(36) PRIMARY KEY,
                client_id VARCHAR(36) NOT NULL REFERENCES time_tracking_clients (id) ON DELETE CASCADE,
                name VARCHAR(500) NOT NULL,
                default_billable_rate NUMERIC(18, 4),
                billable_by_default BOOLEAN NOT NULL DEFAULT TRUE,
                common_for_future_projects BOOLEAN NOT NULL DEFAULT FALSE,
                add_to_existing_projects BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_client_tasks_client
                ON time_tracking_client_tasks (client_id)
            """
        )
    )


async def apply_client_expense_categories_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_client_expense_categories (
                id VARCHAR(36) PRIMARY KEY,
                client_id VARCHAR(36) NOT NULL REFERENCES time_tracking_clients (id) ON DELETE CASCADE,
                name VARCHAR(500) NOT NULL,
                has_unit_price BOOLEAN NOT NULL DEFAULT FALSE,
                is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                sort_order INTEGER,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_client_exp_cat_client
                ON time_tracking_client_expense_categories (client_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_tt_client_exp_cat_active_name
                ON time_tracking_client_expense_categories (client_id, lower(trim(name)))
                WHERE NOT is_archived
            """
        )
    )


async def apply_client_projects_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_client_projects (
                id VARCHAR(36) PRIMARY KEY,
                client_id VARCHAR(36) NOT NULL REFERENCES time_tracking_clients (id) ON DELETE CASCADE,
                name VARCHAR(500) NOT NULL,
                code VARCHAR(64),
                start_date DATE,
                end_date DATE,
                notes TEXT,
                report_visibility VARCHAR(32) NOT NULL DEFAULT 'managers_only',
                project_type VARCHAR(32) NOT NULL DEFAULT 'time_and_materials',
                billable_rate_type VARCHAR(64),
                budget_type VARCHAR(64),
                budget_amount NUMERIC(18, 4),
                budget_hours NUMERIC(12, 2),
                budget_resets_every_month BOOLEAN NOT NULL DEFAULT FALSE,
                budget_includes_expenses BOOLEAN NOT NULL DEFAULT FALSE,
                send_budget_alerts BOOLEAN NOT NULL DEFAULT FALSE,
                budget_alert_threshold_percent NUMERIC(8, 2),
                fixed_fee_amount NUMERIC(18, 4),
                is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_client_projects_client
                ON time_tracking_client_projects (client_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_tt_client_project_code
                ON time_tracking_client_projects (client_id, lower(trim(code)))
                WHERE code IS NOT NULL AND trim(code) <> ''
            """
        )
    )
    await apply_client_projects_billing_columns_patch(conn)
    await apply_client_projects_is_archived_patch(conn)


async def apply_client_projects_is_archived_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_client_projects
            ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )


async def apply_client_projects_billing_columns_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        _PROJECT_BILLING_COLUMN_DEFINITIONS,
    )


async def apply_user_project_access_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_user_project_access (
                id VARCHAR(36) PRIMARY KEY,
                auth_user_id INTEGER NOT NULL REFERENCES time_tracking_users (auth_user_id) ON DELETE CASCADE,
                project_id VARCHAR(36) NOT NULL REFERENCES time_tracking_client_projects (id) ON DELETE CASCADE,
                granted_by_auth_user_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT uq_tt_user_project_access UNIQUE (auth_user_id, project_id)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_upa_user
                ON time_tracking_user_project_access (auth_user_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_upa_project
                ON time_tracking_user_project_access (project_id)
            """
        )
    )


async def apply_time_entries_task_id_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_entries
                ADD COLUMN IF NOT EXISTS task_id VARCHAR(36)
                    REFERENCES time_tracking_client_tasks (id) ON DELETE SET NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entries_project_task
                ON time_tracking_entries (project_id, task_id)
            """
        )
    )


async def apply_reports_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tt_report_saved_views (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(500) NOT NULL,
                owner_user_id INTEGER NOT NULL,
                filters_json TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_rsv_owner ON tt_report_saved_views (owner_user_id)")
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tt_report_snapshots (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(500) NOT NULL,
                report_type VARCHAR(64) NOT NULL,
                group_by VARCHAR(64),
                filters_json TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_by_user_id INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_snap_owner ON tt_report_snapshots (created_by_user_id)")
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tt_report_snapshot_rows (
                id VARCHAR(36) PRIMARY KEY,
                snapshot_id VARCHAR(36) NOT NULL REFERENCES tt_report_snapshots (id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                source_type VARCHAR(64) NOT NULL,
                source_id VARCHAR(64) NOT NULL,
                frozen_data_json TEXT NOT NULL,
                overrides_json TEXT,
                edited_by_user_id INTEGER,
                edited_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_snap_rows_snap ON tt_report_snapshot_rows (snapshot_id)")
    )


async def apply_invoices_schema_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_invoice_counters (
                year INTEGER PRIMARY KEY,
                last_seq INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_invoices (
                id VARCHAR(36) PRIMARY KEY,
                client_id VARCHAR(36) NOT NULL REFERENCES time_tracking_clients (id) ON DELETE RESTRICT,
                project_id VARCHAR(36) REFERENCES time_tracking_client_projects (id) ON DELETE SET NULL,
                invoice_number VARCHAR(64) NOT NULL UNIQUE,
                issue_date DATE NOT NULL,
                due_date DATE NOT NULL,
                currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                subtotal NUMERIC(18, 4) NOT NULL DEFAULT 0,
                discount_percent NUMERIC(8, 4),
                tax_percent NUMERIC(8, 4),
                tax2_percent NUMERIC(8, 4),
                discount_amount NUMERIC(18, 4) NOT NULL DEFAULT 0,
                tax_amount NUMERIC(18, 4) NOT NULL DEFAULT 0,
                total_amount NUMERIC(18, 4) NOT NULL DEFAULT 0,
                amount_paid NUMERIC(18, 4) NOT NULL DEFAULT 0,
                client_note TEXT,
                internal_note TEXT,
                sent_at TIMESTAMPTZ,
                last_sent_at TIMESTAMPTZ,
                viewed_at TIMESTAMPTZ,
                canceled_at TIMESTAMPTZ,
                created_by_auth_user_id INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_invoices_client ON time_tracking_invoices (client_id)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_invoices_project ON time_tracking_invoices (project_id)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_invoices_status ON time_tracking_invoices (status)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_invoices_issue_date ON time_tracking_invoices (issue_date)")
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_invoice_line_items (
                id VARCHAR(36) PRIMARY KEY,
                invoice_id VARCHAR(36) NOT NULL REFERENCES time_tracking_invoices (id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                line_kind VARCHAR(20) NOT NULL,
                description TEXT NOT NULL,
                quantity NUMERIC(18, 6) NOT NULL DEFAULT 1,
                unit_amount NUMERIC(18, 4) NOT NULL DEFAULT 0,
                line_total NUMERIC(18, 4) NOT NULL DEFAULT 0,
                time_entry_id VARCHAR(36),
                expense_request_id VARCHAR(40)
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_tt_inv_lines_invoice ON time_tracking_invoice_line_items (invoice_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_tt_inv_lines_time_entry ON time_tracking_invoice_line_items (time_entry_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_tt_inv_lines_expense ON time_tracking_invoice_line_items (expense_request_id)"
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_invoice_payments (
                id VARCHAR(36) PRIMARY KEY,
                invoice_id VARCHAR(36) NOT NULL REFERENCES time_tracking_invoices (id) ON DELETE CASCADE,
                amount NUMERIC(18, 4) NOT NULL,
                payment_method VARCHAR(64),
                note TEXT,
                recorded_by_auth_user_id INTEGER NOT NULL,
                paid_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_inv_pay_invoice ON time_tracking_invoice_payments (invoice_id)")
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_invoice_audit_logs (
                id SERIAL PRIMARY KEY,
                invoice_id VARCHAR(36) NOT NULL REFERENCES time_tracking_invoices (id) ON DELETE CASCADE,
                action VARCHAR(64) NOT NULL,
                detail TEXT,
                actor_auth_user_id INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_tt_inv_audit_invoice ON time_tracking_invoice_audit_logs (invoice_id)")
    )


async def apply_time_entries_hours_precision_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_entries
            ALTER COLUMN hours TYPE NUMERIC(16,6)
            """
        )
    )


async def apply_project_currency_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        ("currency VARCHAR(10) NOT NULL DEFAULT 'USD'",),
    )


async def apply_time_entries_seconds_and_rounded_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_entries
                ADD COLUMN IF NOT EXISTS duration_seconds INTEGER
            """
        )
    )

    await conn.execute(
        text(
            """
            UPDATE time_tracking_entries
            SET duration_seconds = ROUND(hours * 3600)::INTEGER
            WHERE duration_seconds IS NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_entries
                ALTER COLUMN duration_seconds SET NOT NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_entries
                ADD COLUMN IF NOT EXISTS rounded_hours NUMERIC(16, 6)
            """
        )
    )
    await conn.execute(
        text(
            """
            UPDATE time_tracking_entries
            SET rounded_hours = hours
            WHERE rounded_hours IS NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_entries
                ALTER COLUMN rounded_hours SET NOT NULL
            """
        )
    )


async def apply_time_entries_external_reference_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_entries",
        ("external_reference_url TEXT",),
    )


async def apply_time_entries_manager_void_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_entries",
        (
            "voided_at TIMESTAMPTZ",
            "voided_by_auth_user_id INTEGER",
            "void_kind VARCHAR(32)",
        ),
    )


async def apply_weekly_submissions_schema_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_users",
        ("reports_to_auth_user_id INTEGER",),
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_weekly_submissions (
                id VARCHAR(36) PRIMARY KEY,
                auth_user_id INTEGER NOT NULL
                    REFERENCES time_tracking_users (auth_user_id) ON DELETE CASCADE,
                week_start DATE NOT NULL,
                week_end DATE NOT NULL,
                status VARCHAR(32) NOT NULL,
                auto_submitted_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ,
                CONSTRAINT uq_tt_weekly_sub_user_week UNIQUE (auth_user_id, week_start)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_weekly_sub_user_dates
                ON time_tracking_weekly_submissions (auth_user_id, week_start, week_end)
            """
        )
    )


async def apply_client_projects_project_billable_amount_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        ("project_billable_rate_amount NUMERIC(18, 4)",),
    )


async def apply_hourly_rates_applies_to_project_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            ALTER TABLE time_tracking_user_hourly_rates
                ADD COLUMN IF NOT EXISTS applies_to_project_id VARCHAR(36)
                    REFERENCES time_tracking_client_projects (id) ON DELETE CASCADE
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_hourly_rates_project_scope
                ON time_tracking_user_hourly_rates (applies_to_project_id)
            """
        )
    )

