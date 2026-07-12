"""Chat retention dry-run — COUNT soft-deleted messages only, never DELETE."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _retention_days() -> int:
    raw = (os.getenv("RETENTION_DAYS") or "365").strip()
    try:
        n = int(raw)
    except ValueError:
        return 365
    return max(1, min(n, 3650))


def _dry_run_enabled() -> bool:
    raw = (os.getenv("RETENTION_DRY_RUN") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


async def audit_chat_retention_dry_run(session: AsyncSession) -> dict[str, Any]:
    days = _retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    soft_deleted_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM chat_messages
            WHERE deleted_at IS NOT NULL
              AND deleted_at < :cutoff
            """
        ),
        {"cutoff": cutoff},
    )
    soft_deleted = int(soft_deleted_r.scalar_one() or 0)

    return {
        "readOnly": True,
        "mutatesData": False,
        "dryRun": True,
        "dryRunEnabled": _dry_run_enabled(),
        "retentionDays": days,
        "cutoffIso": cutoff.isoformat(),
        "softDeletedMessagesOlderThanCutoff": soft_deleted,
        "note": (
            "Counts only. No rows are deleted. "
            "Purge requires a separate explicit execute flag + backup + ops approval (not implemented)."
        ),
    }
