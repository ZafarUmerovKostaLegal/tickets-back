from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tarfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infrastructure.config import Settings, database_targets, get_settings

_log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
STATE_NAME = "state.json"


@dataclass
class DatabaseBackupResult:
    name: str
    ok: bool
    file: str | None = None
    size_bytes: int = 0
    error: str | None = None


@dataclass
class BackupManifest:
    id: str
    created_at: str
    status: str
    databases: list[DatabaseBackupResult] = field(default_factory=list)
    media: dict[str, Any] = field(default_factory=dict)
    total_size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now_id() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ"), now.isoformat()


def _run_pg_dump(database_url: str, dest_file: Path, *, timeout_sec: int = 7200) -> None:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pg_dump",
        database_url,
        "-Fc",
        "--no-owner",
        "--no-acl",
        "-f",
        str(dest_file),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env={**os.environ, "PGCONNECT_TIMEOUT": "30"},
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "pg_dump failed").strip()
        if dest_file.exists():
            dest_file.unlink(missing_ok=True)
        raise RuntimeError(err[:2000])


def _archive_media(media_root: Path, dest_file: Path) -> int:
    if not media_root.is_dir():
        raise FileNotFoundError(f"Media directory not found: {media_root}")
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest_file, "w:gz") as tar:
        tar.add(media_root, arcname="media")
    return dest_file.stat().st_size


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_state(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    state_path = Path(s.backup_root) / STATE_NAME
    data = _read_json(state_path)
    if not data:
        return {"last_backup_id": None, "last_status": None, "last_error": None, "running": False}
    return data


def save_state(state: dict[str, Any], settings: Settings | None = None) -> None:
    s = settings or get_settings()
    root = Path(s.backup_root)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / STATE_NAME, state)


def list_backups(settings: Settings | None = None) -> list[dict[str, Any]]:
    s = settings or get_settings()
    root = Path(s.backup_root)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        manifest = _read_json(entry / MANIFEST_NAME)
        if manifest:
            items.append(manifest)
    return items


def apply_retention(settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    root = Path(s.backup_root)
    if not root.is_dir():
        return []

    snapshots: list[tuple[Path, datetime, dict[str, Any]]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        manifest = _read_json(entry / MANIFEST_NAME)
        if not manifest:
            continue
        created_raw = manifest.get("created_at") or ""
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            created = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
        snapshots.append((entry, created, manifest))

    snapshots.sort(key=lambda x: x[1], reverse=True)
    keep_ids = {snap[0].name for snap in snapshots[: max(0, s.backup_retention_min_count)]}
    cutoff = datetime.now(timezone.utc).timestamp() - (s.backup_retention_days * 86400)
    removed: list[str] = []

    for entry, created, _manifest in snapshots:
        if entry.name in keep_ids:
            continue
        if created.timestamp() >= cutoff:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
        _log.info("retention: removed snapshot %s", entry.name)
    return removed


def run_backup(*, settings: Settings | None = None) -> BackupManifest:
    s = settings or get_settings()
    root = Path(s.backup_root)
    root.mkdir(parents=True, exist_ok=True)

    snapshot_id, created_at = _utc_now_id()
    dest = root / snapshot_id
    dest.mkdir(parents=True, exist_ok=False)

    state = load_state(s)
    state.update({"running": True, "last_error": None})
    save_state(state, s)

    db_results: list[DatabaseBackupResult] = []
    total_size = 0

    try:
        for name, url in database_targets(s):
            out = dest / "databases" / f"{name}.dump"
            try:
                _run_pg_dump(url, out)
                size = out.stat().st_size
                db_results.append(DatabaseBackupResult(name=name, ok=True, file=out.name, size_bytes=size))
                total_size += size
                _log.info("backup db ok: %s (%s bytes)", name, size)
            except Exception as ex:
                db_results.append(
                    DatabaseBackupResult(name=name, ok=False, error=str(ex)[:2000])
                )
                _log.exception("backup db failed: %s", name)

        media_info: dict[str, Any] = {"ok": False, "skipped": True}
        if s.backup_media:
            media_root = Path(s.media_path)
            media_file = dest / "media.tar.gz"
            try:
                size = _archive_media(media_root, media_file)
                media_info = {"ok": True, "file": media_file.name, "size_bytes": size}
                total_size += size
                _log.info("backup media ok (%s bytes)", size)
            except Exception as ex:
                media_info = {"ok": False, "error": str(ex)[:2000]}
                _log.exception("backup media failed")

        ok_dbs = sum(1 for r in db_results if r.ok)
        status = "completed"
        if ok_dbs == 0 and not media_info.get("ok"):
            status = "failed"
        elif ok_dbs < len(db_results) or not media_info.get("ok", True):
            status = "partial"

        manifest = BackupManifest(
            id=snapshot_id,
            created_at=created_at,
            status=status,
            databases=db_results,
            media=media_info,
            total_size_bytes=total_size,
        )
        _write_json(dest / MANIFEST_NAME, manifest.to_dict())

        state.update(
            {
                "running": False,
                "last_backup_id": snapshot_id,
                "last_status": status,
                "last_error": None if status != "failed" else "backup failed",
                "last_completed_at": created_at,
            }
        )
        save_state(state, s)
        apply_retention(s)
        return manifest
    except Exception as ex:
        state.update({"running": False, "last_error": str(ex)[:2000]})
        save_state(state, s)
        shutil.rmtree(dest, ignore_errors=True)
        raise


def restore_database(database_url: str, dump_file: Path, *, clean: bool = True) -> None:
    if not dump_file.is_file():
        raise FileNotFoundError(dump_file)
    cmd = ["pg_restore", "-d", database_url, str(dump_file), "--no-owner", "--no-acl"]
    if clean:
        cmd.extend(["--clean", "--if-exists"])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "pg_restore failed").strip()
        if "errors ignored on restore" in err.lower():
            return
        raise RuntimeError(err[:2000])


def restore_media(archive_file: Path, media_root: Path) -> None:
    if not archive_file.is_file():
        raise FileNotFoundError(archive_file)
    media_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_file, "r:gz") as tar:
        tar.extractall(path=media_root.parent)
