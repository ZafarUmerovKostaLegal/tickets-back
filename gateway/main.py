from backend_common.logging import configure_logging

configure_logging("gateway")


from presentation.api import app
