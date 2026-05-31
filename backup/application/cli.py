from __future__ import annotations

import argparse
import sys
from pathlib import Path

from application.backup_runner import apply_retention, list_backups, restore_database, restore_media, run_backup
from infrastructure.config import database_targets, get_settings


def cmd_run_once(_: argparse.Namespace) -> int:
    manifest = run_backup()
    print(f"Backup {manifest.id}: status={manifest.status}, size={manifest.total_size_bytes}")
    return 0 if manifest.status != "failed" else 1


def cmd_list(_: argparse.Namespace) -> int:
    for item in list_backups():
        print(f"{item.get('id')}  {item.get('status')}  {item.get('created_at')}  {item.get('total_size_bytes')} bytes")
    return 0


def cmd_retention(_: argparse.Namespace) -> int:
    removed = apply_retention()
    print(f"Removed snapshots: {len(removed)}")
    for name in removed:
        print(f"  - {name}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    settings = get_settings()
    root = settings.backup_root
    snap_dir = f"{root.rstrip('/')}/{args.snapshot_id}"
    base = Path(snap_dir)
    if not base.is_dir():
        print(f"Snapshot not found: {base}", file=sys.stderr)
        return 1

    if args.confirm.strip() != "RESTORE_BACKUP":
        print("Specify --confirm RESTORE_BACKUP", file=sys.stderr)
        return 1

    targets = {name: url for name, url in database_targets(settings)}
    if args.database:
        if args.database not in targets:
            print(f"Unknown database {args.database!r}", file=sys.stderr)
            return 1
        dump = base / "databases" / f"{args.database}.dump"
        print(f"Restoring {args.database} from {dump}")
        restore_database(targets[args.database], dump, clean=not args.no_clean)
        print("Done")
        return 0

    if args.media_only:
        media_archive = base / "media.tar.gz"
        print(f"Restoring media from {media_archive}")
        restore_media(media_archive, Path(settings.media_path))
        print("Done")
        return 0

    for name, url in targets.items():
        dump = base / "databases" / f"{name}.dump"
        if not dump.is_file():
            print(f"[skip] {name}: dump missing")
            continue
        print(f"Restoring {name}...")
        restore_database(url, dump, clean=not args.no_clean)
    media_archive = base / "media.tar.gz"
    if media_archive.is_file():
        print("Restoring media...")
        restore_media(media_archive, Path(settings.media_path))
    print("Restore completed")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backup service CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("run-once", help="Create backup snapshot now").set_defaults(func=cmd_run_once)
    sub.add_parser("list", help="List backup snapshots").set_defaults(func=cmd_list)
    sub.add_parser("retention", help="Apply retention policy").set_defaults(func=cmd_retention)

    pr = sub.add_parser("restore", help="Restore from snapshot")
    pr.add_argument("snapshot_id", type=str)
    pr.add_argument("--database", type=str, default="", help="Restore only one database")
    pr.add_argument("--media-only", action="store_true")
    pr.add_argument("--no-clean", action="store_true", help="Do not drop objects before restore")
    pr.add_argument("--confirm", type=str, default="")
    pr.set_defaults(func=cmd_restore)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
