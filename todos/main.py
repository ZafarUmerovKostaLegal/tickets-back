from backend_common.logging import configure_logging
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty
from infrastructure.config import get_settings
from infrastructure.token_crypto import warn_if_outlook_fernet_key_empty

configure_logging("todos")
_settings = get_settings()
warn_if_ws_internal_secret_empty(_settings.ws_internal_secret, service="todos")
warn_if_outlook_fernet_key_empty(_settings.outlook_token_fernet_key, service="todos")

from presentation.api import app
