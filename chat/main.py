from backend_common.db_password_warn import warn_if_database_url_uses_default_password
from backend_common.logging import configure_logging
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty
from infrastructure.config import get_settings, resolve_database_url

configure_logging("chat")
warn_if_ws_internal_secret_empty(get_settings().ws_internal_secret, service="chat")
warn_if_database_url_uses_default_password(
    resolve_database_url(get_settings()),
    service="chat",
)

from presentation.api import app
