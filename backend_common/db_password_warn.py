"""Warn when Postgres URL still uses a known compose default password."""

from __future__ import annotations

import logging
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Default passwords from docker-compose / historical local setups.
_DEFAULT_PG_PASSWORDS = frozenset(
    {
        "gateway",
        "time_tracking",
        "auth",
        "tickets",
        "todos",
        "chat",
        "expenses",
        "vacation",
        "notifications",
        "attendance",
        "inventory",
        "correspondence",
        "postgres",
        "password",
        "changeme",
        "user",
    }
)


def warn_if_database_url_uses_default_password(url: str | None, *, service: str) -> None:
    raw = (url or "").strip()
    if not raw:
        return
    try:
        parsed = urlparse(raw)
    except Exception:
        return
    password = unquote(parsed.password or "")
    if not password:
        logger.warning(
            "%s: DATABASE_URL has no password — set a unique password in production.",
            service,
        )
        return
    if password.casefold() in {p.casefold() for p in _DEFAULT_PG_PASSWORDS}:
        logger.warning(
            "%s: DATABASE_URL still uses a default/local password (%s). "
            "Rotate to a unique strong password in production (no data wipe required).",
            service,
            password,
        )
