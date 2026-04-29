from backend_common.logging import configure_logging

configure_logging("auth")

from presentation.api import app
