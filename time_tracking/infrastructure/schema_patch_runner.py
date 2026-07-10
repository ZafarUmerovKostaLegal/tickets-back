"""Idempotent schema patch ledger — additive, no data loss.

Patches are recorded in tt_schema_patch_log after first successful apply.
Re-runs skip logged patches (safe for multi-replica startup). New patches in
code still run once. All patch SQL uses IF NOT EXISTS / additive DDL.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

PatchFn = Callable[[AsyncConnection], Awaitable[None]]


async def ensure_patch_log_table(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tt_schema_patch_log (
                patch_name VARCHAR(128) PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )


async def _is_patch_applied(conn: AsyncConnection, patch_name: str) -> bool:
    r = await conn.execute(
        text("SELECT 1 FROM tt_schema_patch_log WHERE patch_name = :name LIMIT 1"),
        {"name": patch_name},
    )
    return r.scalar() is not None


async def _mark_patch_applied(conn: AsyncConnection, patch_name: str) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO tt_schema_patch_log (patch_name)
            VALUES (:name)
            ON CONFLICT (patch_name) DO NOTHING
            """
        ),
        {"name": patch_name},
    )


async def apply_registered_schema_patches(
    conn: AsyncConnection,
    patches: Sequence[tuple[str, PatchFn]],
) -> None:
    await ensure_patch_log_table(conn)
    for name, fn in patches:
        if await _is_patch_applied(conn, name):
            continue
        logger.info("Applying TT schema patch: %s", name)
        await fn(conn)
        await _mark_patch_applied(conn, name)
