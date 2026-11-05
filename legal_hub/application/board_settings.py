from __future__ import annotations

import re

BUILTIN_COLUMNS = (
    "todo",
    "progress",
    "waitClient",
    "waitGov",
    "associate",
    "partner",
    "changes",
    "done",
)
FOLDER_COLORS = frozenset({"slate", "ink", "accent", "warning", "success", "error"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SCOPE_RE = re.compile(r"^(?:mine|queue|project:[A-Za-z0-9][A-Za-z0-9_-]{0,63})$")
_MAX_COLUMNS = 40
_MAX_FOLDERS = 40
_MAX_PROJECTS = 200


def _clean_id(value: object, limit: int = 40) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if len(text) > limit or not _ID_RE.match(text):
        return ""
    return text


def _clip(value: object, limit: int) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text[:limit]


def default_columns() -> dict:
    return {
        "custom": [],
        "order": list(BUILTIN_COLUMNS),
        "collapsed": ["done"],
    }


def normalize_columns(raw: object) -> dict:
    body = raw if isinstance(raw, dict) else {}
    custom: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in body.get("custom") or []:
        if not isinstance(item, dict) or len(custom) >= _MAX_COLUMNS:
            continue
        column_id = _clean_id(item.get("id"))
        title = _clip(item.get("title"), 80)
        if not column_id or not title or column_id in BUILTIN_COLUMNS or column_id in seen:
            continue
        seen.add(column_id)
        custom.append({"id": column_id, "title": title})
    known = set(BUILTIN_COLUMNS) | seen
    order: list[str] = []
    for item in body.get("order") or []:
        column_id = _clean_id(item)
        if column_id in known and column_id not in order:
            order.append(column_id)
    for column_id in [*BUILTIN_COLUMNS, *(column["id"] for column in custom)]:
        if column_id not in order:
            order.append(column_id)
    collapsed: list[str] = []
    for item in body.get("collapsed") or []:
        column_id = _clean_id(item)
        if column_id in known and column_id not in collapsed:
            collapsed.append(column_id)
    return {"custom": custom, "order": order, "collapsed": collapsed}


def default_folders() -> dict:
    return {"hideLocked": False, "folders": [], "sidebarOrder": []}


def _clean_names(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = _clip(item, 120)
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _sidebar_order(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    tokens: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "folder":
            folder_id = _clean_id(item.get("id"))
            key = f"folder:{folder_id}"
            if not folder_id or key in seen:
                continue
            seen.add(key)
            tokens.append({"type": "folder", "id": folder_id})
            continue
        if kind == "client":
            name = _clip(item.get("name"), 120)
            key = f"client:{name.casefold()}"
            if key in seen:
                continue
            seen.add(key)
            tokens.append({"type": "client", "name": name})
    return tokens


def normalize_folders(raw: object) -> dict:
    body = raw if isinstance(raw, dict) else {}
    folders: list[dict] = []
    seen_ids: set[str] = set()
    owned: set[str] = set()
    for item in body.get("folders") or []:
        if not isinstance(item, dict) or len(folders) >= _MAX_FOLDERS:
            continue
        folder_id = _clean_id(item.get("id"))
        if not folder_id or folder_id in seen_ids:
            continue
        seen_ids.add(folder_id)
        project_ids: list[str] = []
        for project_id in item.get("projectIds") or []:
            cleaned = _clean_id(project_id, 64)
            if not cleaned or cleaned in owned or cleaned in project_ids or len(project_ids) >= _MAX_PROJECTS:
                continue
            owned.add(cleaned)
            project_ids.append(cleaned)
        color = item.get("color")
        folders.append({
            "id": folder_id,
            "name": _clip(item.get("name"), 120),
            "color": color if isinstance(color, str) and color in FOLDER_COLORS else "slate",
            "projectIds": project_ids,
            "collapsed": item.get("collapsed") is True,
            "clientOrder": _clean_names(item.get("clientOrder")),
        })
    return {
        "hideLocked": body.get("hideLocked") is True,
        "folders": folders,
        "sidebarOrder": _sidebar_order(body.get("sidebarOrder")),
    }


def default_filter(scope: str) -> dict:
    return {
        "scope": scope,
        "onlyLate": False,
        "onlyUrgent": False,
        "project": "",
        "status": "",
        "stream": "",
        "owner": "",
        "priority": "",
    }


def normalize_scope(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _SCOPE_RE.match(text):
        raise ValueError("Неизвестный раздел доски")
    return text


def normalize_filter(raw: object, scope: str | None = None) -> dict:
    body = raw if isinstance(raw, dict) else {}
    chosen = normalize_scope(scope if scope is not None else body.get("scope"))
    return {
        "scope": chosen,
        "onlyLate": body.get("onlyLate") is True,
        "onlyUrgent": body.get("onlyUrgent") is True,
        "project": _clip(body.get("project"), 80),
        "status": _clip(body.get("status"), 40),
        "stream": _clip(body.get("stream"), 80),
        "owner": _clip(body.get("owner"), 40),
        "priority": _clip(body.get("priority"), 20),
    }
