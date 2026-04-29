from backend_common.logging import configure_logging

configure_logging("tickets")

from presentation.api import app
