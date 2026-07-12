"""TT schema patch ledger — re-exports shared runner with TT table name."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncConnection

from backend_common.schema_patch_runner import (
    PatchFn,
    apply_registered_schema_patches as _apply,
    ensure_patch_log_table as _ensure,
)

TT_PATCH_LOG_TABLE = "tt_schema_patch_log"


async def ensure_patch_log_table(conn: AsyncConnection) -> None:
    await _ensure(conn, table_name=TT_PATCH_LOG_TABLE)


async def apply_registered_schema_patches(
    conn: AsyncConnection,
    patches: Sequence[tuple[str, PatchFn]],
) -> None:
    await _apply(conn, patches, table_name=TT_PATCH_LOG_TABLE, log_prefix="TT")
