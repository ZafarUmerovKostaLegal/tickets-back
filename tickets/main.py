from backend_common.logging import configure_logging
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty
from infrastructure.config import get_settings

configure_logging("tickets")
warn_if_ws_internal_secret_empty(get_settings().ws_internal_secret, service="tickets")

from presentation.api import app
