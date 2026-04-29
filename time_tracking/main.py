from backend_common.logging import configure_logging

configure_logging("time_tracking")

from presentation.api import app
