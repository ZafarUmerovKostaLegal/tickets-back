from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from backend_common.db_probe import probe_postgresql, probe_redis, redact_database_url
from infrastructure.database_targets import DatabaseMonitorSettings, database_targets
from presentation.routes.users import require_admin

router = APIRouter(prefix="/ops", tags=["ops"])


def _overall_status(items: list[dict]) -> str:
    if not items:
        return "empty"
    if any(item.get("status") == "error" for item in items):
        return "degraded"
    if any(item.get("load") == "high" for item in items):
        return "busy"
    return "ok"


@router.get("/databases", summary="Живая карта PostgreSQL и Redis (только администраторы)")
async def databases_overview(_: dict = Depends(require_admin)):
    settings = DatabaseMonitorSettings()
    targets = database_targets(settings)
    probes = [probe_postgresql(name, url) for name, url in targets]
    if settings.redis_url.strip():
        probes.append(probe_redis(settings.redis_url.strip()))
    results = await asyncio.gather(*probes) if probes else []
    items = list(results)
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
