import os

from backend_common.db_password_warn import warn_if_database_url_uses_default_password
from backend_common.logging import configure_logging
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty
from infrastructure.config import get_settings

configure_logging("kosta_daily")
_settings = get_settings()
warn_if_ws_internal_secret_empty(_settings.ws_internal_secret, service="kosta_daily")
if not _settings.chat_disable_db:
    warn_if_database_url_uses_default_password(
        os.environ.get("DATABASE_URL") or _settings.database_url,
        service="kosta_daily",
    )

from presentation.api import app

__all__ = ["app"]
