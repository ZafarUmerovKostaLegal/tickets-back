"""Warn when WS_INTERNAL_SECRET is empty (fail-closed auth still applies)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def warn_if_ws_internal_secret_empty(secret: str | None, *, service: str) -> None:
    if (secret or "").strip():
        return
    logger.warning(
        "%s: WS_INTERNAL_SECRET is empty — internal WS/push and initials API "
        "are disabled (fail-closed). Set WS_INTERNAL_SECRET in production.",
        service,
    )
