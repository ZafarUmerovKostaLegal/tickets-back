

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
    "progress_budget_amount NUMERIC(18, 4)",
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
            ALTER TABLE time_tracking_users
                ADD COLUMN IF NOT EXISTS can_transfer_time_without_project_access
                    BOOLEAN NOT NULL DEFAULT FALSE
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
                project_id VARCHAR(36) NOT NULL REFERENCES time_tracking_client_projects (id) ON DELETE CASCADE,
                name VARCHAR(500) NOT NULL,
                default_billable_rate NUMERIC(18, 4),
                billable_by_default BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'time_tracking_client_tasks'
                      AND column_name = 'project_id'
                ) THEN
                    CREATE INDEX IF NOT EXISTS ix_tt_client_tasks_project
                        ON time_tracking_client_tasks (project_id);
                END IF;
            END $$;
            """
        )
    )


async def apply_client_tasks_project_scope_migration(conn: AsyncConnection) -> None:

    r = await conn.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'time_tracking_client_tasks'
            )
            """
        )
    )
    if not (r.scalar() or False):
        return
    cols = await conn.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'time_tracking_client_tasks'
            """
        )
    )
    colset = {row[0] for row in cols.fetchall()}
    if "client_id" in colset:
        await conn.execute(text("ALTER TABLE time_tracking_client_tasks ADD COLUMN IF NOT EXISTS project_id VARCHAR(36)"))
        await conn.execute(
            text(
                """
                UPDATE time_tracking_client_tasks AS t
                SET project_id = p.id
                FROM (
                    SELECT DISTINCT ON (client_id) id, client_id
                    FROM time_tracking_client_projects
                    ORDER BY client_id, created_at ASC NULLS LAST, id ASC
                ) AS p
                WHERE t.project_id IS NULL AND t.client_id = p.client_id
                """
            )
        )
        await conn.execute(text("DELETE FROM time_tracking_client_tasks WHERE project_id IS NULL"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_tt_client_tasks_client"))
        await conn.execute(text("ALTER TABLE time_tracking_client_tasks DROP COLUMN IF EXISTS client_id"))
        await conn.execute(text("ALTER TABLE time_tracking_client_tasks ALTER COLUMN project_id SET NOT NULL"))
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'time_tracking_client_tasks_project_id_fkey'
                    ) THEN
                        ALTER TABLE time_tracking_client_tasks
                            ADD CONSTRAINT time_tracking_client_tasks_project_id_fkey
                            FOREIGN KEY (project_id)
                            REFERENCES time_tracking_client_projects (id) ON DELETE CASCADE;
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text("ALTER TABLE time_tracking_client_tasks DROP COLUMN IF EXISTS common_for_future_projects")
        )
        await conn.execute(
            text("ALTER TABLE time_tracking_client_tasks DROP COLUMN IF EXISTS add_to_existing_projects")
        )

    await conn.execute(
        text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'time_tracking_client_tasks'
                      AND column_name = 'project_id'
                ) THEN
                    CREATE INDEX IF NOT EXISTS ix_tt_client_tasks_project
                        ON time_tracking_client_tasks (project_id);
                END IF;
            END $$;
            """
        )
    )


async def apply_client_tasks_flat_fee_schema_patch(conn: AsyncConnection) -> None:
    """Add per-task flat fee billing (e.g. My mehnat registration = 230000 UZS per entry)."""
    await add_columns_if_missing(
        conn,
        "time_tracking_client_tasks",
        (
            "billing_mode VARCHAR(20) NOT NULL DEFAULT 'hourly'",
            "flat_fee_amount NUMERIC(18, 4)",
            "flat_fee_currency VARCHAR(10)",
        ),
    )
    await conn.execute(
        text(
            """
            UPDATE time_tracking_client_tasks
            SET billing_mode = 'flat_fee',
                flat_fee_amount = 230000,
                flat_fee_currency = 'UZS'
            WHERE lower(trim(name)) = 'my mehnat registration'
              AND (
                    billing_mode IS DISTINCT FROM 'flat_fee'
                 OR flat_fee_amount IS DISTINCT FROM 230000
                 OR coalesce(flat_fee_currency, '') IS DISTINCT FROM 'UZS'
              )
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


async def apply_client_projects_is_paused_patch(conn: AsyncConnection) -> None:
    """Temporary pause: blocks new time entries without archiving the project."""
    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        ("is_paused BOOLEAN NOT NULL DEFAULT FALSE",),
    )


async def apply_client_projects_skip_partner_invoice_confirmation_patch(conn: AsyncConnection) -> None:
    """Exception projects: create invoices without fully_confirmed partner period."""
    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        ("skip_partner_invoice_confirmation BOOLEAN NOT NULL DEFAULT FALSE",),
    )


async def apply_client_projects_billing_columns_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        _PROJECT_BILLING_COLUMN_DEFINITIONS,
    )


async def apply_client_projects_hour_package_patch(conn: AsyncConnection) -> None:
    """Monthly hour package (N hours for $X) + overage at employee rates."""
    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        (
            "package_hours_per_month NUMERIC(12, 2)",
            "package_fee_amount NUMERIC(18, 4)",
        ),
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


async def apply_time_entries_project_date_index_patch(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entries_project_date
                ON time_tracking_entries (project_id, work_date)
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
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tt_report_partner_confirmation_requests (
                id VARCHAR(36) PRIMARY KEY,
                snapshot_id VARCHAR(36) NOT NULL REFERENCES tt_report_snapshots (id) ON DELETE CASCADE,
                project_id VARCHAR(36) NOT NULL REFERENCES time_tracking_client_projects (id) ON DELETE CASCADE,
                date_from DATE NOT NULL,
                date_to DATE NOT NULL,
                title VARCHAR(700) NOT NULL,
                status VARCHAR(32) NOT NULL,
                review_priority VARCHAR(16) NOT NULL DEFAULT 'yellow',
                submitted_by_auth_user_id INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ,
                CONSTRAINT uq_tt_report_partner_conf_snap_proj_period
                    UNIQUE (snapshot_id, project_id, date_from, date_to)
            )
            """
        )
    )
    await add_columns_if_missing(
        conn,
        "tt_report_partner_confirmation_requests",
        [
            "review_priority VARCHAR(16) NOT NULL DEFAULT 'yellow'",
        ],
    )
    await conn.execute(
        text(
            """
            UPDATE tt_report_partner_confirmation_requests
            SET review_priority = 'yellow'
            WHERE review_priority IS NULL OR TRIM(review_priority) = ''
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_rpconf_submitter
                ON tt_report_partner_confirmation_requests (submitted_by_auth_user_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_rpconf_project_status
                ON tt_report_partner_confirmation_requests (project_id, status)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_rpconf_status_priority_created
                ON tt_report_partner_confirmation_requests (status, review_priority, created_at)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tt_report_partner_confirmation_signatures (
                id VARCHAR(36) PRIMARY KEY,
                request_id VARCHAR(36) NOT NULL
                    REFERENCES tt_report_partner_confirmation_requests (id) ON DELETE CASCADE,
                partner_auth_user_id INTEGER NOT NULL,
                confirmed_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT uq_tt_report_partner_conf_sig UNIQUE (request_id, partner_auth_user_id)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_rpconf_sig_partner
                ON tt_report_partner_confirmation_signatures (partner_auth_user_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tt_report_partner_confirmation_comments (
                id VARCHAR(36) PRIMARY KEY,
                request_id VARCHAR(36) NOT NULL
                    REFERENCES tt_report_partner_confirmation_requests (id) ON DELETE CASCADE,
                auth_user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_rpconf_comments_request_created
                ON tt_report_partner_confirmation_comments (request_id, created_at)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_rpconf_comments_author
                ON tt_report_partner_confirmation_comments (auth_user_id)
            """
        )
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
    await add_columns_if_missing(
        conn,
        "time_tracking_invoices",
        (
            "payment_confirmation_document_url TEXT",
            "payment_confirmation_recorded_at TIMESTAMPTZ",
        ),
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


async def apply_time_entries_scope_color_patch(conn: AsyncConnection) -> None:
    """Scope highlight color for report preview rows (#RRGGBB)."""
    await add_columns_if_missing(
        conn,
        "time_tracking_entries",
        ("scope_color VARCHAR(7)",),
    )


async def apply_project_scope_definitions_patch(conn: AsyncConnection) -> None:
    """Descriptions for Scope colors, unique within a project."""
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_project_scope_definitions (
                project_id VARCHAR(36) NOT NULL REFERENCES time_tracking_client_projects(id) ON DELETE CASCADE,
                color VARCHAR(7) NOT NULL,
                description TEXT NOT NULL,
                created_by_auth_user_id INTEGER,
                updated_by_auth_user_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ,
                PRIMARY KEY (project_id, color)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_project_scope_project
                ON time_tracking_project_scope_definitions (project_id)
            """
        )
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
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_time_entry_edit_unlocks (
                id VARCHAR(36) PRIMARY KEY,
                auth_user_id INTEGER NOT NULL
                    REFERENCES time_tracking_users (auth_user_id) ON DELETE CASCADE,
                work_date DATE NOT NULL,
                granted_by_auth_user_id INTEGER NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT uq_tt_te_unlock_user_day UNIQUE (auth_user_id, work_date)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_te_unlock_exp
                ON time_tracking_time_entry_edit_unlocks (expires_at)
            """
        )
    )


async def apply_client_projects_project_billable_amount_patch(conn: AsyncConnection) -> None:

    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        ("project_billable_rate_amount NUMERIC(18, 4)",),
    )


async def apply_client_projects_records_language_patch(conn: AsyncConnection) -> None:
    await add_columns_if_missing(
        conn,
        "time_tracking_client_projects",
        ("records_language VARCHAR(3) NOT NULL DEFAULT 'ENG'",),
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


async def apply_project_scoped_billable_rates_open_interval_patch(conn: AsyncConnection) -> None:
    """Project-scoped billable rates must not be limited by project start/end dates."""

    await conn.execute(
        text(
            """
            UPDATE time_tracking_user_hourly_rates
            SET valid_from = NULL,
                valid_to = NULL
            WHERE rate_kind = 'billable'
              AND applies_to_project_id IS NOT NULL
            """
        )
    )


async def apply_firm_bank_profiles_schema_patch(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_firm_bank_profiles (
                id VARCHAR(36) PRIMARY KEY,
                title VARCHAR(255) NOT NULL DEFAULT '',
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                tin TEXT NOT NULL DEFAULT '',
                bank_name TEXT NOT NULL DEFAULT '',
                bank_address TEXT NOT NULL DEFAULT '',
                account_currency VARCHAR(16) NOT NULL DEFAULT 'EUR',
                account_number TEXT NOT NULL DEFAULT '',
                bank_code TEXT NOT NULL DEFAULT '',
                swift TEXT NOT NULL DEFAULT '',
                correspondent_bank TEXT NOT NULL DEFAULT '',
                correspondent_account TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_by_auth_user_id INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_firm_bank_default
                ON time_tracking_firm_bank_profiles (is_default)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_firm_bank_currency
                ON time_tracking_firm_bank_profiles (account_currency)
            """
        )
    )


async def apply_time_tracking_teams_schema_patch(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_teams (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                partner_auth_user_id INTEGER NOT NULL
                    REFERENCES time_tracking_users (auth_user_id) ON DELETE RESTRICT,
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
            CREATE INDEX IF NOT EXISTS ix_tt_teams_partner
                ON time_tracking_teams (partner_auth_user_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_tt_teams_active_name
                ON time_tracking_teams (lower(trim(name)))
                WHERE NOT is_archived
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_team_members (
                team_id VARCHAR(36) NOT NULL
                    REFERENCES time_tracking_teams (id) ON DELETE CASCADE,
                auth_user_id INTEGER NOT NULL
                    REFERENCES time_tracking_users (auth_user_id) ON DELETE CASCADE,
                PRIMARY KEY (team_id, auth_user_id)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_team_members_team
                ON time_tracking_team_members (team_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_team_members_user
                ON time_tracking_team_members (auth_user_id)
            """
        )
    )


async def apply_report_performance_indexes_patch(conn: AsyncConnection) -> None:
    """Add composite indexes that speed up the time-report queries."""
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entries_date_voided
                ON time_tracking_entries (work_date, voided_at)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entries_voided_at
                ON time_tracking_entries (voided_at)
            """
        )
    )


async def apply_time_entry_archives_patch(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_entry_archives (
                id VARCHAR(36) PRIMARY KEY,
                time_entry_id VARCHAR(36) NOT NULL,
                auth_user_id INTEGER NOT NULL,
                project_id VARCHAR(36),
                client_id VARCHAR(36),
                duplicate_group_id TEXT,
                archived_at TIMESTAMPTZ NOT NULL,
                archived_by_auth_user_id INTEGER NOT NULL,
                restored_at TIMESTAMPTZ,
                restored_by_auth_user_id INTEGER,
                payload TEXT NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entry_archives_project
                ON time_tracking_entry_archives (project_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entry_archives_entry
                ON time_tracking_entry_archives (time_entry_id)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entry_archives_restored
                ON time_tracking_entry_archives (restored_at)
            """
        )
    )


async def apply_time_entries_project_id_fk_patch(conn: AsyncConnection) -> None:
    """Hard FK entries.project_id → projects (RESTRICT). Safe only when orphans == 0.

    If orphans exist, raises without modifying data — fix via /integrity/audit first.
    """
    orphan_r = await conn.execute(
        text(
            """
            SELECT COUNT(*) FROM time_tracking_entries e
            WHERE e.project_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM time_tracking_client_projects p
                WHERE p.id = e.project_id
              )
            """
        )
    )
    orphans = int(orphan_r.scalar_one() or 0)
    if orphans > 0:
        raise RuntimeError(
            f"Refusing FK fk_tt_entries_project_id: {orphans} orphan entry.project_id "
            "refs. Fix manually (no auto-delete); re-check GET /integrity/audit. "
            "No schema change was applied."
        )

    await conn.execute(
        text(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'fk_tt_entries_project_id'
                ) THEN
                    ALTER TABLE time_tracking_entries
                        ADD CONSTRAINT fk_tt_entries_project_id
                        FOREIGN KEY (project_id)
                        REFERENCES time_tracking_client_projects (id)
                        ON DELETE RESTRICT;
                END IF;
            END $$
            """
        )
    )


async def apply_invoice_document_overrides_patch(conn: AsyncConnection) -> None:
    """Persist invoice preview/document field overrides (legal page, cover, time report)."""
    await add_columns_if_missing(
        conn,
        "time_tracking_invoices",
        ("document_overrides_json TEXT",),
    )


async def apply_invoice_partner_billing_and_fx_patch(conn: AsyncConnection) -> None:
    """Partner billing period on invoices, FX audit on lines, FX rates table."""
    await add_columns_if_missing(
        conn,
        "time_tracking_invoices",
        (
            "partner_billing_period_from DATE",
            "partner_billing_period_to DATE",
            "partner_confirmation_request_id VARCHAR(36)",
        ),
    )
    await add_columns_if_missing(
        conn,
        "time_tracking_invoice_line_items",
        (
            "source_currency VARCHAR(10)",
            "source_amount NUMERIC(18, 4)",
            "fx_rate NUMERIC(18, 8)",
        ),
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS time_tracking_fx_rates (
                id VARCHAR(36) PRIMARY KEY,
                from_currency VARCHAR(10) NOT NULL,
                to_currency VARCHAR(10) NOT NULL,
                rate_date DATE NOT NULL,
                rate NUMERIC(18, 8) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_fx_pair_date
                ON time_tracking_fx_rates (from_currency, to_currency, rate_date)
            """
        )
    )


async def apply_partner_confirmation_review_priority_patch(conn: AsyncConnection) -> None:
    """Приоритет проверки отчётов (red/yellow/green) — additive column на существующую таблицу."""
    await add_columns_if_missing(
        conn,
        "tt_report_partner_confirmation_requests",
        [
            "review_priority VARCHAR(16) NOT NULL DEFAULT 'yellow'",
        ],
    )
    await conn.execute(
        text(
            """
            UPDATE tt_report_partner_confirmation_requests
            SET review_priority = 'yellow'
            WHERE review_priority IS NULL OR TRIM(review_priority) = ''
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_rpconf_status_priority_created
                ON tt_report_partner_confirmation_requests (status, review_priority, created_at)
            """
        )
    )


async def apply_time_entries_active_partial_indexes_patch(conn: AsyncConnection) -> None:
    """Partial indexes for active (non-voided) entries — additive only, no row deletes."""
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entries_active_project_date
                ON time_tracking_entries (project_id, work_date)
                WHERE voided_at IS NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_entries_active_work_date
                ON time_tracking_entries (work_date)
                WHERE voided_at IS NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tt_rpconf_status_updated
                ON tt_report_partner_confirmation_requests (status, updated_at DESC)
            """
        )
    )


REGISTERED_SCHEMA_PATCHES: tuple[tuple[str, object], ...] = (
    ("team_workload", apply_team_workload_schema_patch),
    ("time_manager_clients", apply_time_manager_clients_schema_patch),
    ("client_extra_contacts", apply_client_extra_contacts_schema_patch),
    ("client_expense_categories", apply_client_expense_categories_schema_patch),
    ("client_projects", apply_client_projects_schema_patch),
    ("client_tasks", apply_client_tasks_schema_patch),
    ("client_tasks_project_scope", apply_client_tasks_project_scope_migration),
    ("client_tasks_flat_fee", apply_client_tasks_flat_fee_schema_patch),
    ("user_project_access", apply_user_project_access_patch),
    ("time_entries_task_id", apply_time_entries_task_id_schema_patch),
    ("time_entries_project_date_index", apply_time_entries_project_date_index_patch),
    ("time_entries_hours_precision", apply_time_entries_hours_precision_patch),
    ("reports", apply_reports_schema_patch),
    ("partner_confirmation_review_priority", apply_partner_confirmation_review_priority_patch),
    ("invoices", apply_invoices_schema_patch),
    ("invoice_partner_billing_and_fx", apply_invoice_partner_billing_and_fx_patch),
    ("invoice_document_overrides", apply_invoice_document_overrides_patch),
    ("project_currency", apply_project_currency_patch),
    ("time_entries_seconds_rounded", apply_time_entries_seconds_and_rounded_patch),
    ("time_entries_external_reference", apply_time_entries_external_reference_patch),
    ("time_entries_scope_color", apply_time_entries_scope_color_patch),
    ("project_scope_definitions", apply_project_scope_definitions_patch),
    ("time_entries_manager_void", apply_time_entries_manager_void_patch),
    ("weekly_submissions", apply_weekly_submissions_schema_patch),
    ("client_projects_billable_amount", apply_client_projects_project_billable_amount_patch),
    ("client_projects_hour_package", apply_client_projects_hour_package_patch),
    ("client_projects_records_language", apply_client_projects_records_language_patch),
    ("client_projects_is_paused", apply_client_projects_is_paused_patch),
    (
        "client_projects_skip_partner_invoice_confirmation",
        apply_client_projects_skip_partner_invoice_confirmation_patch,
    ),
    ("hourly_rates_applies_to_project", apply_hourly_rates_applies_to_project_patch),
    ("project_scoped_billable_rates", apply_project_scoped_billable_rates_open_interval_patch),
    ("time_tracking_teams", apply_time_tracking_teams_schema_patch),
    ("firm_bank_profiles", apply_firm_bank_profiles_schema_patch),
    ("report_performance_indexes", apply_report_performance_indexes_patch),
    ("time_entry_archives", apply_time_entry_archives_patch),
    ("time_entries_project_id_fk", apply_time_entries_project_id_fk_patch),
    ("time_entries_active_partial_indexes", apply_time_entries_active_partial_indexes_patch),
)

