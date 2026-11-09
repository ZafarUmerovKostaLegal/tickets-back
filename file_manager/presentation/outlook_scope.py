"""Restrict Outlook add-in tokens to filing-related File Manager routes."""

from __future__ import annotations

import re

# Method + path relative to /api/v1/files
_OUTLOOK_ALLOWED: list[tuple[str, re.Pattern[str]]] = [
    ("GET", re.compile(r"^/clients/?$")),
    ("GET", re.compile(r"^/matters/[^/]+/browse/?$")),
    ("POST", re.compile(r"^/matters/[^/]+/folders/?$")),
    ("POST", re.compile(r"^/matters/[^/]+/documents/?$")),
]


def outlook_path_allowed(method: str, path: str) -> bool:
    """path is FastAPI request.url.path (may include /api/v1/files prefix)."""
    normalized = path or ""
    prefix = "/api/v1/files"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix) :] or "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    m = (method or "GET").upper()
    return any(m == allowed_method and pattern.match(normalized) for allowed_method, pattern in _OUTLOOK_ALLOWED)
