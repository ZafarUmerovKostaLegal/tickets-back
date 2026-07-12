import os

from backend_common.db_password_warn import warn_if_database_url_uses_default_password
from backend_common.logging import configure_logging

configure_logging("attendance")
warn_if_database_url_uses_default_password(os.environ.get("DATABASE_URL"), service="attendance")

from presentation.api import app
