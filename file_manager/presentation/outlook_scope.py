"""Restrict Outlook add-in tokens to filing-related File Manager routes."""

from __future__ import annotations

import re

# Method + path relative to /api/v1/files
_OUTLOOK_ALLOWED: list[tuple[str, re.Pattern[str]]] = [
    ("GET", re.compile(r"^/clients/?$")),
    ("GET", re.compile(r"^/matters/[^/]+/browse/?$")),
    ("POST", re.compile(r"^/matters/[^/]+/folders/?$")),
    ("POST", re.compile(r"^/matters/[^/]+/documents/?$")),
    # MacroView-style pane: search, favourites, recent, filing status and suggestions.
    ("GET", re.compile(r"^/search/?$")),
    ("GET", re.compile(r"^/recent/?$")),
    ("GET", re.compile(r"^/favorites/?$")),
    ("PUT", re.compile(r"^/favorites/?$")),
    ("DELETE", re.compile(r"^/favorites/[^/]+/[^/]+/?$")),
    ("POST", re.compile(r"^/mail/lookup/?$")),
    ("GET", re.compile(r"^/mail/suggestions/?$")),
    # Server-side mail rules and the owner's auto-filing switch.
    ("GET", re.compile(r"^/mail/rules/?$")),
    ("POST", re.compile(r"^/mail/rules/?$")),
    ("PATCH", re.compile(r"^/mail/rules/[^/]+/?$")),
    ("DELETE", re.compile(r"^/mail/rules/[^/]+/?$")),
    ("PUT", re.compile(r"^/mail/autofile/?$")),
    # Teams meeting side panel: link the meeting to a matter folder for its recordings.
    ("GET", re.compile(r"^/meetings/link/?$")),
    ("PUT", re.compile(r"^/meetings/link/?$")),
    ("DELETE", re.compile(r"^/meetings/link/?$")),
    ("POST", re.compile(r"^/meetings/link/check/?$")),
    # Thread file update and attaching filed documents to a draft.
    ("GET", re.compile(r"^/documents/[^/]+/content/?$")),
    ("POST", re.compile(r"^/documents/[^/]+/(checkout|checkin)/?$")),
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
