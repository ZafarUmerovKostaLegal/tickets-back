from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException

from application.backup_runner import list_backups, load_state, run_backup
from infrastructure.config import database_targets, get_settings

router = APIRouter(prefix="/api/v1/backup", tags=["backup"])


def _check_token(authorization: str | None, x_backup_token: str | None) -> None:
    settings = get_settings()
    expected = (settings.backup_api_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="BACKUP_API_TOKEN is not configured",
        )
    token = (x_backup_token or "").strip()
    if not token and authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid backup token")


@router.get("/status")
async def backup_status():
    state = load_state()
    targets = database_targets()
    return {
        "service": "backup",
        "state": state,
        "configured_databases": [name for name, _ in targets],
        "schedule_cron": get_settings().backup_schedule_cron,
        "retention_days": get_settings().backup_retention_days,
        "retention_min_count": get_settings().backup_retention_min_count,
    }


@router.get("/snapshots")
async def backup_snapshots():
    return {"items": list_backups()}


@router.post("/run")
async def backup_run_now(
    authorization: str | None = Header(default=None),
    x_backup_token: str | None = Header(default=None, alias="X-Backup-Token"),
):
    _check_token(authorization, x_backup_token)
    state = load_state()
    if state.get("running"):
        raise HTTPException(status_code=409, detail="Backup already running")
    manifest = await asyncio.to_thread(run_backup)
    return manifest.to_dict()
