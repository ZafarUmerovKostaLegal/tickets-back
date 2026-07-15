"""Shared CORS origin helper — never returns '*' (incompatible with credentials)."""

from __future__ import annotations

import os

_KNOWN_PRODUCTION_ORIGINS = (
    "https://tickets.kostalegal.com",
    "https://www.tickets.kostalegal.com",
)

_LOCAL_DEFAULTS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:8081",
    "http://127.0.0.1:8081",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
)


def resolve_cors_origins(
    *,
    frontend_url: str | None = None,
    environment: str | None = None,
    include_local_defaults: bool = True,
) -> list[str]:
    """Build an explicit allowlist. Empty / '*' entries are ignored."""
    _ = environment  # kept for call-site compatibility
    raw_frontend = (
        frontend_url
        if frontend_url is not None
        else (os.getenv("FRONTEND_URL") or os.getenv("CORS_ORIGINS") or "")
    ).strip()

    origins: list[str] = []
    if raw_frontend and raw_frontend != "*":
        for part in raw_frontend.split(","):
            u = part.strip()
            if u and u != "*":
                origins.append(u)

    # Always allow known production frontends (needed with credentials: include).
    for o in _KNOWN_PRODUCTION_ORIGINS:
        if o not in origins:
            origins.append(o)

    if include_local_defaults:
        for o in _LOCAL_DEFAULTS:
            if o not in origins:
                origins.append(o)

    if not origins:
        origins = list(_KNOWN_PRODUCTION_ORIGINS) + list(_LOCAL_DEFAULTS)

    return [o for o in dict.fromkeys(origins) if o and o != "*"]
