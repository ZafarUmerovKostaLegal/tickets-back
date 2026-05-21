from backend_common.logging import configure_logging

configure_logging("chat")

from presentation.api import app
