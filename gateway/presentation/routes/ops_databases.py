from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional

from backend_common.db_probe import probe_postgresql, probe_redis, redact_database_url
from infrastructure.database_targets import DatabaseMonitorSettings, database_targets
from presentation.routes.users import (
    ADMIN_ROLE,
    MAIN_ADMIN_ROLE,
    _get_current_user_optional,
)
from fastapi import Header

router = APIRouter(prefix="/ops", tags=["ops"])


async def require_ops_databases_admin(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Main Admin / Administrator only — Partner cannot see DB topology."""
    user = await _get_current_user_optional(request, authorization)
    role = (user.get("role") or "").strip()
    if role not in {MAIN_ADMIN_ROLE, ADMIN_ROLE}:
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator or Administrator can view database ops",
        )
    return user


def _overall_status(items: list[dict]) -> str:
    if not items:
        return "empty"
    if any(item.get("status") == "error" for item in items):
        return "degraded"
    if any(item.get("load") == "high" for item in items):
        return "busy"
    return "ok"


def _scrub_probe_errors(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        copy = dict(item)
        if copy.get("status") == "error" and copy.get("error"):
            copy["error"] = "connection failed"
        out.append(copy)
    return out


@router.get("/databases", summary="Живая карта PostgreSQL и Redis (только администраторы)")
async def databases_overview(_: dict = Depends(require_ops_databases_admin)):
    if os.getenv("OPS_DATABASES_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        raise HTTPException(status_code=404, detail="Ops databases endpoint disabled")

    settings = DatabaseMonitorSettings()
    targets = database_targets(settings)
    probes = [probe_postgresql(name, url) for name, url in targets]
    if settings.redis_url.strip():
        probes.append(probe_redis(settings.redis_url.strip()))
    results = await asyncio.gather(*probes) if probes else []
    items = _scrub_probe_errors(list(results))
    return {
        "status": _overall_status(items),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(items),
            "ok": sum(1 for item in items if item.get("status") == "ok"),
            "errors": sum(1 for item in items if item.get("status") == "error"),
            "highLoad": sum(1 for item in items if item.get("load") == "high"),
            "notConfigured": sum(1 for item in items if item.get("status") == "not_configured"),
        },
        "targets": [
            {
                "name": name,
                "urlRedacted": redact_database_url(url),
            }
            for name, url in targets
        ]
        + (
            [{"name": "redis", "urlRedacted": redact_database_url(settings.redis_url.strip())}]
            if settings.redis_url.strip()
            else []
        ),
        "databases": items,
    }
