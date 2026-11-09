"""Clients / matters / project access read from the time-tracking service."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from infrastructure.config import get_settings

_log = logging.getLogger(__name__)

_DIRECTORY_TTL = 60.0
_ACCESS_TTL = 30.0


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    archived: bool


@dataclass(frozen=True)
class Matter:
    id: str
    client_id: str
    name: str
    code: str
    archived: bool


@dataclass(frozen=True)
class Directory:
    clients: dict[str, Client]
    matters: dict[str, Matter]


class DirectoryUnavailable(RuntimeError):
    pass


_directory_cache: tuple[float, Directory] | None = None
_access_cache: dict[int, tuple[float, frozenset[str]]] = {}


def _base() -> str:
    return (get_settings().time_tracking_service_url or "").strip().rstrip("/")


def _items(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("data") or []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _flag(row: dict, *keys: str) -> bool:
    return any(bool(row.get(key)) for key in keys)


def _text(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


async def _get(path: str, authorization: str) -> object:
    base = _base()
    if not base:
        raise DirectoryUnavailable("TIME_TRACKING_SERVICE_URL is not configured")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{base}{path}", headers={"Authorization": authorization})
    except httpx.RequestError as exc:
        raise DirectoryUnavailable(str(exc)) from exc
    if response.status_code >= 400:
        raise DirectoryUnavailable(f"{path} -> {response.status_code}")
    return response.json()


async def load_directory(authorization: str, *, force: bool = False) -> Directory:
    global _directory_cache
    now = time.monotonic()
    if not force and _directory_cache and now - _directory_cache[0] < _DIRECTORY_TTL:
        return _directory_cache[1]
    clients_raw = await _get("/clients?includeArchived=true", authorization)
    projects_raw = await _get("/projects?includeArchived=true&includeBudgetMetrics=false", authorization)
    clients: dict[str, Client] = {}
    for row in _items(clients_raw):
        cid = _text(row, "id")
        if not cid:
            continue
        clients[cid] = Client(id=cid, name=_text(row, "name") or cid, archived=_flag(row, "is_archived", "isArchived"))
    matters: dict[str, Matter] = {}
    for row in _items(projects_raw):
        mid = _text(row, "id")
        cid = _text(row, "client_id", "clientId")
        if not mid or not cid:
            continue
        matters[mid] = Matter(
            id=mid,
            client_id=cid,
            name=_text(row, "name") or mid,
            code=_text(row, "code"),
            archived=_flag(row, "is_archived", "isArchived"),
        )
    directory = Directory(clients=clients, matters=matters)
    _directory_cache = (now, directory)
    return directory


async def my_project_ids(authorization: str, user_id: int) -> frozenset[str]:
    now = time.monotonic()
    cached = _access_cache.get(user_id)
    if cached and now - cached[0] < _ACCESS_TTL:
        return cached[1]
    try:
        payload = await _get(f"/users/{user_id}/project-access", authorization)
    except DirectoryUnavailable as exc:
        _log.info("tt project access unavailable user=%s err=%s", user_id, exc)
        return cached[1] if cached else frozenset()
    ids = (payload.get("projectIds") or payload.get("project_ids") or []) if isinstance(payload, dict) else []
    result = frozenset(str(item) for item in ids if str(item).strip())
    _access_cache[user_id] = (now, result)
    return result


async def project_assignees(authorization: str, project_id: str) -> list[dict]:
    try:
        payload = await _get(f"/projects/{project_id}/time-tracking-assignees", authorization)
    except DirectoryUnavailable as exc:
        _log.info("tt assignees unavailable project=%s err=%s", project_id, exc)
        return []
    rows = payload.get("assignees") if isinstance(payload, dict) else payload
    out: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if _flag(row, "is_archived", "isArchived", "is_blocked", "isBlocked"):
            continue
        try:
            uid = int(row.get("auth_user_id") or row.get("authUserId"))
        except (TypeError, ValueError):
            continue
        out.append({
            "userId": uid,
            "name": _text(row, "display_name", "displayName", "email") or f"#{uid}",
            "position": _text(row, "position"),
        })
    return out


def invalidate_access(user_id: int | None = None) -> None:
    if user_id is None:
        _access_cache.clear()
    else:
        _access_cache.pop(user_id, None)
