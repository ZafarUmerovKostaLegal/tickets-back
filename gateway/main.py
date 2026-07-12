from backend_common.db_password_warn import warn_if_database_url_uses_default_password
from backend_common.jwt_secret_warn import warn_if_jwt_secret_weak
from backend_common.logging import configure_logging
from backend_common.redis_url_warn import warn_if_redis_url_unauthenticated
from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty
from infrastructure.config import get_settings
from infrastructure.database_targets import DatabaseMonitorSettings

configure_logging("gateway")
_settings = get_settings()
warn_if_ws_internal_secret_empty(_settings.ws_internal_secret, service="gateway")
warn_if_jwt_secret_weak(getattr(_settings, "jwt_secret", None) or "", service="gateway")

_db = DatabaseMonitorSettings()
warn_if_redis_url_unauthenticated(_db.redis_url, service="gateway")
for _name, _url in (
    ("gateway-auth-db", _db.auth_database_url),
    ("gateway-tickets-db", _db.tickets_database_url),
    ("gateway-notifications-db", _db.notifications_database_url),
    ("gateway-inventory-db", _db.inventory_database_url),
    ("gateway-attendance-db", _db.attendance_database_url),
    ("gateway-todos-db", _db.todos_database_url),
    ("gateway-time-tracking-db", _db.time_tracking_database_url),
    ("gateway-expenses-db", _db.expenses_database_url),
    ("gateway-vacation-db", _db.vacation_database_url),
    ("gateway-chat-db", _db.chat_database_url),
    ("gateway-correspondence-db", _db.correspondence_database_url),
):
    warn_if_database_url_uses_default_password(_url, service=_name)

from presentation.api import app
