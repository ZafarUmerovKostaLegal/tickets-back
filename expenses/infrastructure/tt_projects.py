from __future__ import annotations

import logging

import httpx

from infrastructure.config import get_settings

_log = logging.getLogger(__name__)


def _normalize_authorization_header(raw: str | None) -> str | None:
    a = (raw or "").strip()
    if not a:
        return None
    if a.lower().startswith("bearer "):
        return a
    return f"Bearer {a}"


async def fetch_tt_project_label(
    project_id: str | None,
    *,
    authorization: str | None = None,
) -> str | None:
    """Resolve TT project id → «Name (Client)» for expense emails."""
    pid = (project_id or "").strip()
    if not pid:
        return None
    base = (get_settings().time_tracking_service_url or "").strip().rstrip("/")
    if not base:
        return None
    hdr = _normalize_authorization_header(authorization)
    headers = {"Authorization": hdr} if hdr else {}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{base}/projects/{pid}/ref", headers=headers)
    except httpx.RequestError as exc:
        _log.debug("tt project ref unreachable id=%s err=%s", pid, exc)
        return None
    if r.status_code >= 400:
        _log.debug("tt project ref status=%s id=%s", r.status_code, pid)
        return None
    try:
        data = r.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or "").strip()
    if not name:
        return None
    client = str(data.get("clientName") or data.get("client_name") or "").strip()
    code = str(data.get("code") or "").strip()
    label = name
    if code:
        label = f"{name} ({code})"
    if client:
        label = f"{label} · {client}"
    return label
