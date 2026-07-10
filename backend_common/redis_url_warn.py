"""Warn when Redis URL has no password (Celery broker / shared cache)."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def redis_url_has_password(url: str | None) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    return bool(parsed.password)


def warn_if_redis_url_unauthenticated(url: str | None, *, service: str) -> None:
    raw = (url or "").strip()
    if not raw:
        return
    if redis_url_has_password(raw):
        return
    logger.warning(
        "%s: REDIS_URL has no password — Redis is reachable without auth on the "
        "Docker network. Set REDIS_PASSWORD + redis://:PASSWORD@host:6379/0 on production.",
        service,
    )
