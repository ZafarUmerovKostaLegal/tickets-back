"""Additive vacation schema patches — IF NOT EXISTS / DROP NOT NULL only."""

from __future__ import annotations

from backend_common.schema_patch_runner import PatchFn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def apply_vacation_auth_user_and_absence_fks(conn: AsyncConnection) -> None:
    for stmt in (
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
        "ALTER TABLE absence_days ADD COLUMN IF NOT EXISTS manual_entry_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_absence_days_manual_entry_id ON absence_days(manual_entry_id)",
    ):
        await conn.execute(text(stmt))


async def apply_leave_request_final_decision(conn: AsyncConnection) -> None:
    """Вторая ступень согласования: решение управляющего партнёра."""
    for stmt in (
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS final_decision_at TIMESTAMPTZ",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS final_decision_reason TEXT",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS final_decided_by_user_id INTEGER",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS pdf_doc_version INTEGER NOT NULL DEFAULT 0",
    ):
        await conn.execute(text(stmt))


REGISTERED_VACATION_SCHEMA_PATCHES: list[tuple[str, PatchFn]] = [
    ("vacation_auth_user_and_absence_fks", apply_vacation_auth_user_and_absence_fks),
    ("vacation_leave_request_final_decision", apply_leave_request_final_decision),
]
