"""Kosta File add-in presence: every open add-in reports itself, managers see who is connected."""

from __future__ import annotations

import re
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.models import AddinClientModel
from presentation.common import iso, now, require_manager
from presentation.deps import Viewer, get_viewer

router = APIRouter(tags=["file-manager"])

HOSTS = frozenset({"outlook", "word", "excel", "powerpoint", "unknown"})
PLATFORMS = frozenset({"windows", "mac", "web", "ios", "android", "unknown"})
AUTH_METHODS = frozenset({"token", "cookie", "sso", "dialog", "unknown"})
SSO_STATUSES = frozenset({"ok", "unsupported", "failed", "consent", "unknown"})
# An open pane reports every few minutes; quiet longer than this means it is closed.
ONLINE_WINDOW = timedelta(minutes=10)
RETENTION = timedelta(days=180)
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class Heartbeat(BaseModel):
    clientId: str = Field(min_length=8, max_length=64)
    host: str = Field(default="unknown", max_length=20)
    platform: str = Field(default="unknown", max_length=20)
    officeVersion: str = Field(default="", max_length=40)
    addinVersion: str = Field(default="", max_length=20)
    authMethod: str = Field(default="unknown", max_length=20)
    ssoStatus: str = Field(default="unknown", max_length=20)
    ssoError: str = Field(default="", max_length=300)


def _pick(value: str, allowed: frozenset[str]) -> str:
    value = (value or "").strip().lower()
    return value if value in allowed else "unknown"


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return (forwarded or (request.client.host if request.client else ""))[:64]


@router.post("/addin/heartbeat")
async def addin_heartbeat(
    body: Heartbeat,
    request: Request,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    client_id = body.clientId.strip()
    if not _CLIENT_ID.match(client_id):
        return {"ok": False}
    stamp = now()
    row = await session.get(AddinClientModel, client_id)
    if row is None:
        row = AddinClientModel(client_id=client_id, first_seen=stamp)
        session.add(row)
    row.user_id = viewer.user_id
    row.user_name = (viewer.name or "")[:300]
    row.host = _pick(body.host, HOSTS)
    row.platform = _pick(body.platform, PLATFORMS)
    row.office_version = body.officeVersion.strip()[:40]
    row.addin_version = body.addinVersion.strip()[:20]
    row.auth_method = _pick(body.authMethod, AUTH_METHODS)
    row.sso_status = _pick(body.ssoStatus, SSO_STATUSES)
    row.sso_error = body.ssoError.strip()[:300] if row.sso_status not in {"ok", "unknown"} else ""
    row.user_agent = (request.headers.get("user-agent") or "")[:400]
    row.ip = _client_ip(request)
    row.last_seen = stamp
    await session.execute(delete(AddinClientModel).where(AddinClientModel.last_seen < stamp - RETENTION))
    await session.commit()
    return {"ok": True}


@router.get("/admin/addin-clients")
async def addin_clients(
    days: int = Query(default=30, ge=1, le=180),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    await require_manager(viewer)
    stamp = now()
    rows = list(await session.scalars(
        select(AddinClientModel)
        .where(AddinClientModel.last_seen >= stamp - timedelta(days=days))
        .order_by(AddinClientModel.last_seen.desc())
    ))

    def online(row: AddinClientModel) -> bool:
        seen = row.last_seen if row.last_seen.tzinfo else row.last_seen.replace(tzinfo=stamp.tzinfo)
        return stamp - seen <= ONLINE_WINDOW

    clients = [{
        "clientId": row.client_id,
        "userId": row.user_id,
        "userName": row.user_name,
        "host": row.host,
        "platform": row.platform,
        "officeVersion": row.office_version,
        "addinVersion": row.addin_version,
        "authMethod": row.auth_method,
        "ssoStatus": row.sso_status,
        "ssoError": row.sso_error,
        "ip": row.ip,
        "firstSeen": iso(row.first_seen),
        "lastSeen": iso(row.last_seen),
        "online": online(row),
    } for row in rows]
    return {
        "clients": clients,
        "summary": {
            "online": sum(1 for c in clients if c["online"]),
            "onlineUsers": len({c["userId"] for c in clients if c["online"]}),
            "users": len({c["userId"] for c in clients}),
            "installs": len(clients),
            "ssoProblems": sum(1 for c in clients if c["ssoStatus"] in {"failed", "unsupported", "consent"}),
        },
    }
