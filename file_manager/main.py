from backend_common.logging import configure_logging

configure_logging("file_manager")

from presentation.api import app

__all__ = ["app"]
