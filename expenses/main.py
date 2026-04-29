import logging
import os
import sys


def _configure_logging() -> None:

    raw = (os.getenv("LOG_LEVEL") or "").strip()
    level_name = (raw or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


_configure_logging()

from presentation.api import app
