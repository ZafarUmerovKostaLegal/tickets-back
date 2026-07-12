import logging
import os
import sys

from backend_common.db_password_warn import warn_if_database_url_uses_default_password
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty


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
warn_if_ws_internal_secret_empty(os.environ.get("WS_INTERNAL_SECRET"), service="expenses")
warn_if_database_url_uses_default_password(os.environ.get("DATABASE_URL"), service="expenses")

from presentation.api import app
