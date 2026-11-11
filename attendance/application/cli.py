"""CLI for attendance camera-event ingest.

Examples:
  python -m application.cli backfill --from 2026-01-01 --to 2026-12-31
  python -m application.cli backfill --year 2026
  python -m application.cli import-csv path/to/Transaction.csv
  python -m application.cli status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path

# Load tickets-back/.env before importing infrastructure (DATABASE_URL etc.).
_BACK_ROOT = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv

    load_dotenv(_BACK_ROOT / ".env")
    load_dotenv(_BACK_ROOT / ".env.local", override=True)
except ImportError:
    pass

# Prefer ATTENDANCE_DATABASE_URL; rewrite docker hostname for host-side CLI.
if not os.environ.get("DATABASE_URL"):
    att = (os.environ.get("ATTENDANCE_DATABASE_URL") or "").strip()
    if att:
        os.environ["DATABASE_URL"] = att
_db = os.environ.get("DATABASE_URL") or ""
if "attendance_db" in _db and os.environ.get("ATTENDANCE_DB_HOST_OVERRIDE"):
    os.environ["DATABASE_URL"] = _db.replace("attendance_db", os.environ["ATTENDANCE_DB_HOST_OVERRIDE"])

from application.import_transaction_csv import import_transaction_csv
from application.ingest_camera_events import ingest_date_range
from infrastructure.camera_events_repo import camera_events_time_bounds, count_camera_events
from infrastructure.database import async_session_factory, engine, Base
from infrastructure import models  # noqa: F401


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("attendance.cli")


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def cmd_backfill(date_from: date, date_to: date, max_records: int) -> int:
    await _ensure_schema()
    logger.info("Backfill camera events %s .. %s", date_from, date_to)

    def on_day(day_res) -> None:
        print(
            f"  {day_res.day}: fetched={day_res.fetched} upserted={day_res.upserted}"
            + (f" errors={day_res.device_errors}" if day_res.device_errors else ""),
            flush=True,
        )

    result = await ingest_date_range(
        date_from,
        date_to,
        max_records_per_device=max_records,
        pause_sec=0.2,
        progress=on_day,
    )
    print(
        f"Done: days={result.days} fetched={result.fetched} upserted={result.upserted} "
        f"errors={len(result.device_errors)}",
        flush=True,
    )
    if result.device_errors:
        for err in result.device_errors[:20]:
            print(f"  ! {err}", flush=True)
    return 0 if result.fetched > 0 or not result.device_errors else 1


async def cmd_status() -> int:
    await _ensure_schema()
    async with async_session_factory() as session:
        total = await count_camera_events(session)
        mn, mx = await camera_events_time_bounds(session)
    print(f"events={total}")
    print(f"min_event_time={mn}")
    print(f"max_event_time={mx}")
    return 0


async def cmd_import_csv(path: Path, camera_ip: str | None) -> int:
    await _ensure_schema()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1
    result = await import_transaction_csv(path, camera_ip=camera_ip)
    print(
        f"CSV import done: read={result.rows_read} mapped={result.rows_mapped} "
        f"unmapped={result.rows_unmapped} upserted={result.upserted} "
        f"camera_ip={result.camera_ip} range={result.date_min}..{result.date_max}",
        flush=True,
    )
    if result.unmapped_names:
        print("Unmapped names:", flush=True)
        for name, count in sorted(result.unmapped_names.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count}", flush=True)
    print("Name map:", flush=True)
    for name, emp in sorted(result.name_map.items()):
        print(f"  {name} -> {emp}", flush=True)
    return 0 if result.upserted > 0 or result.rows_mapped == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attendance camera events ingest CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backfill = sub.add_parser("backfill", help="Pull camera events into DB for a date range")
    p_backfill.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD")
    p_backfill.add_argument("--to", dest="date_to", default=None, help="YYYY-MM-DD")
    p_backfill.add_argument("--year", type=int, default=None, help="Shortcut: full calendar year")
    p_backfill.add_argument("--max-records", type=int, default=10000, help="Max records per device per day")

    p_csv = sub.add_parser("import-csv", help="Import Hikvision Transaction CSV into camera_events")
    p_csv.add_argument("path", help="Path to Transaction_*.csv")
    p_csv.add_argument("--camera-ip", default=None, help="Store under this camera IP (default: first host)")

    sub.add_parser("status", help="Show DB event counts / time bounds")

    args = parser.parse_args(argv)

    if args.command == "status":
        return asyncio.run(cmd_status())

    if args.command == "import-csv":
        return asyncio.run(cmd_import_csv(Path(args.path), args.camera_ip))

    if args.command == "backfill":
        if args.year:
            date_from = date(args.year, 1, 1)
            date_to = date(args.year, 12, 31)
        else:
            if not args.date_from or not args.date_to:
                parser.error("backfill requires --year YEAR or both --from and --to")
            date_from = date.fromisoformat(args.date_from)
            date_to = date.fromisoformat(args.date_to)
        today = date.today()
        if date_to > today:
            date_to = today
        return asyncio.run(cmd_backfill(date_from, date_to, max(1, min(10000, args.max_records))))

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
