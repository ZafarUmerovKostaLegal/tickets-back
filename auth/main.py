from backend_common.db_password_warn import warn_if_database_url_uses_default_password
from backend_common.logging import configure_logging
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty
from infrastructure.config import get_settings

configure_logging("auth")
_settings = get_settings()
warn_if_ws_internal_secret_empty(_settings.ws_internal_secret, service="auth")
warn_if_database_url_uses_default_password(_settings.database_url, service="auth")

from presentation.api import app
