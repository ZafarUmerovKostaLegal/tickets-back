"""Shared CORS origin helper."""

from __future__ import annotations


def resolve_cors_origins(
    *,
    frontend_url: str | None = None,
    environment: str | None = None,
    include_local_defaults: bool = True,
) -> list[str]:
    """Allow all origins (explicit request to restore ``*`` allowlist)."""
    _ = (frontend_url, environment, include_local_defaults)
    return ["*"]
