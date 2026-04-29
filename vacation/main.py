from backend_common.logging import configure_logging

configure_logging("vacation")

from presentation.api import app
