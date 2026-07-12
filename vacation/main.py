import os

from backend_common.db_password_warn import warn_if_database_url_uses_default_password
from backend_common.logging import configure_logging
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty

configure_logging("vacation")
warn_if_ws_internal_secret_empty(os.environ.get("WS_INTERNAL_SECRET"), service="vacation")
warn_if_database_url_uses_default_password(os.environ.get("DATABASE_URL"), service="vacation")

from presentation.api import app
