"""Idempotent schema patch ledger — additive DDL only, no data loss.

Patches are recorded after first successful apply. Re-runs skip logged patches
(safe for multi-replica startup). New patches in code still run once.
All patch SQL must use IF NOT EXISTS / additive DDL — never DROP DATA.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

PatchFn = Callable[[AsyncConnection], Awaitable[None]]

DEFAULT_PATCH_LOG_TABLE = "schema_patch_log"


async def ensure_patch_log_table(
    conn: AsyncConnection,
    *,
    table_name: str = DEFAULT_PATCH_LOG_TABLE,
) -> None:
    # table_name is internal constant only — never pass user input
    await conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                patch_name VARCHAR(128) PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )


async def _is_patch_applied(
    conn: AsyncConnection,
    patch_name: str,
    *,
    table_name: str = DEFAULT_PATCH_LOG_TABLE,
) -> bool:
    r = await conn.execute(
        text(f"SELECT 1 FROM {table_name} WHERE patch_name = :name LIMIT 1"),
        {"name": patch_name},
    )
    return r.scalar() is not None


async def _mark_patch_applied(
    conn: AsyncConnection,
    patch_name: str,
    *,
    table_name: str = DEFAULT_PATCH_LOG_TABLE,
) -> None:
    await conn.execute(
        text(
            f"""
            INSERT INTO {table_name} (patch_name)
            VALUES (:name)
            ON CONFLICT (patch_name) DO NOTHING
            """
        ),
        {"name": patch_name},
    )


async def apply_registered_schema_patches(
    conn: AsyncConnection,
    patches: Sequence[tuple[str, PatchFn]],
    *,
    table_name: str = DEFAULT_PATCH_LOG_TABLE,
    log_prefix: str = "schema",
) -> None:
    await ensure_patch_log_table(conn, table_name=table_name)
    for name, fn in patches:
        if await _is_patch_applied(conn, name, table_name=table_name):
            continue
        logger.info("Applying %s schema patch: %s", log_prefix, name)
        await fn(conn)
        await _mark_patch_applied(conn, name, table_name=table_name)
