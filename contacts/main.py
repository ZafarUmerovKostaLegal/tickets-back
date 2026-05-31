from backend_common.logging import configure_logging

configure_logging("contacts")

from presentation.api import app
