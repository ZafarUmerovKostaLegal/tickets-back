from backend_common.logging import configure_logging

configure_logging("telegram_bot")

from presentation.api import app
