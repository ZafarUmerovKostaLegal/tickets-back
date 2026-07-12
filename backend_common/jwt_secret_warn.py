"""Warn when JWT_SECRET is empty or too short (gateway does not hard-fail)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def warn_if_jwt_secret_weak(secret: str | None, *, service: str, min_len: int = 32) -> None:
    raw = (secret or "").strip()
    if not raw:
        logger.warning(
            "%s: JWT_SECRET is empty — set a strong secret shared with auth (NEEDS_OPS).",
            service,
        )
        return
    if len(raw) < min_len:
        logger.warning(
            "%s: JWT_SECRET length %s < %s — use a longer secret on production.",
            service,
            len(raw),
            min_len,
        )
