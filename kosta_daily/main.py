from backend_common.logging import configure_logging

configure_logging("kosta_daily")

from presentation.api import app

__all__ = ["app"]
