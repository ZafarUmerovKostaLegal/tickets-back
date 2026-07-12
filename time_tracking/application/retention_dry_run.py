"""Retention dry-run — COUNT only, never DELETE.

Controlled by RETENTION_DRY_RUN (default on) and RETENTION_DAYS (default 365).
Execute/purge is intentionally not implemented here.
"""

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


async def audit_tt_retention_dry_run(session: AsyncSession) -> dict[str, Any]:
    days = _retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    archives_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM time_tracking_entry_archives
            WHERE restored_at IS NULL
              AND archived_at < :cutoff
            """
        ),
        {"cutoff": cutoff},
    )
    archives_old = int(archives_r.scalar_one() or 0)

    snapshots_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM tt_report_snapshots
            WHERE created_at < :cutoff
            """
        ),
        {"cutoff": cutoff},
    )
    snapshots_old = int(snapshots_r.scalar_one() or 0)

    return {
        "readOnly": True,
        "mutatesData": False,
        "dryRun": True,
        "dryRunEnabled": _dry_run_enabled(),
        "retentionDays": days,
        "cutoffIso": cutoff.isoformat(),
        "entryArchivesOlderThanCutoff": archives_old,
        "reportSnapshotsOlderThanCutoff": snapshots_old,
        "note": (
            "Counts only. No rows are deleted. "
            "Purge requires a separate explicit execute flag + backup + ops approval (not implemented)."
        ),
    }
