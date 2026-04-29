from backend_common.logging import configure_logging

configure_logging("attendance")

from presentation.api import app
