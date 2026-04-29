from backend_common.logging import configure_logging

configure_logging("todos")

from presentation.api import app
