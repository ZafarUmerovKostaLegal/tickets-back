import os

from backend_common.db_password_warn import warn_if_database_url_uses_default_password
from backend_common.logging import configure_logging
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty
from infrastructure.config import get_settings

configure_logging("notifications")
warn_if_ws_internal_secret_empty(get_settings().ws_internal_secret, service="notifications")
warn_if_database_url_uses_default_password(
    os.environ.get("DATABASE_URL") or getattr(get_settings(), "database_url", None),
    service="notifications",
)

from presentation.api import app
