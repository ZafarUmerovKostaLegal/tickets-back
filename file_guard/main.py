from backend_common.logging import configure_logging

configure_logging("file_guard")

from presentation.api import app
