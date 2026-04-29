from backend_common.logging import configure_logging

configure_logging("inventory")

from presentation.api import app
