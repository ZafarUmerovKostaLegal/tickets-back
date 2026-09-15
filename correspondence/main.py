import logging
import os
import sys

from backend_common.db_password_warn import warn_if_database_url_uses_default_password


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
warn_if_database_url_uses_default_password(os.environ.get("DATABASE_URL"), service="correspondence")

from infrastructure.config import get_settings
from infrastructure.correspondence_mail import smtp_ready, smtp_status_summary

_settings = get_settings()
if smtp_ready(_settings):
    logging.getLogger(__name__).info(
        "correspondence SMTP ready (%s)",
        smtp_status_summary(_settings),
    )
else:
    logging.getLogger(__name__).warning(
        "correspondence SMTP NOT configured — email notify will be skipped (%s). "
        "Pass EXPENSE_SMTP_* (or CORRESPONDENCE_SMTP_*) into the correspondence container.",
        smtp_status_summary(_settings),
    )

from presentation.api import app
