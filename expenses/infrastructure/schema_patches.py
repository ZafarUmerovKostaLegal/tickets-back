"""Additive expenses schema patches — IF NOT EXISTS only.

Destructive legacy int-PK DROP stays outside this ledger (see presentation/api.py).
"""

from __future__ import annotations

from backend_common.schema_patch_runner import PatchFn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def apply_expense_request_extra_columns(conn: AsyncConnection) -> None:
    for ddl in (
        "ALTER TABLE expense_requests ADD COLUMN IF NOT EXISTS payment_deadline DATE",
        "ALTER TABLE expense_requests ADD COLUMN IF NOT EXISTS paid_by_user_id INTEGER",
        "ALTER TABLE expense_attachments ADD COLUMN IF NOT EXISTS attachment_kind VARCHAR(64)",
        "ALTER TABLE expense_requests ADD COLUMN IF NOT EXISTS expense_category_id VARCHAR(64)",
        "ALTER TABLE expense_requests ADD COLUMN IF NOT EXISTS partner_user_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_expense_requests_expense_category_id ON expense_requests (expense_category_id)",
        "CREATE INDEX IF NOT EXISTS ix_expense_requests_partner_user_id ON expense_requests (partner_user_id)",
    ):
        await conn.execute(text(ddl))


REGISTERED_EXPENSE_SCHEMA_PATCHES: list[tuple[str, PatchFn]] = [
    ("expense_request_extra_columns", apply_expense_request_extra_columns),
]
