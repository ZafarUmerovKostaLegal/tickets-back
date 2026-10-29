from backend_common.logging import configure_logging

configure_logging("legal_hub")

from presentation.api import app

__all__ = ["app"]
