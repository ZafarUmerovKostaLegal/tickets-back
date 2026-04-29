

from __future__ import annotations

import logging
import os


def _normalize_level(raw: str | None) -> int:
    level_name = (raw or "").strip().upper() or "INFO"
    return getattr(logging, level_name, logging.INFO)


def configure_logging(service_name: str) -> None:

    root = logging.getLogger()
    level = _normalize_level(os.getenv("LOG_LEVEL") or os.getenv("UVICORN_LOG_LEVEL"))
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    else:
        root.setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger(service_name).debug("logging configured")
