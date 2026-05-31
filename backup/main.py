from backend_common.logging import configure_logging

configure_logging("backup")

from presentation.api import app
