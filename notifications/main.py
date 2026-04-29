from backend_common.logging import configure_logging

configure_logging("notifications")

from presentation.api import app
