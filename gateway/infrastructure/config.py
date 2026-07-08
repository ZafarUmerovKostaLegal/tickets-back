from functools import lru_cache

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings


_DEFAULT_SERVICE_URLS: dict[str, str] = {
    "auth_service_url": "http://auth:1236",
    "tickets_service_url": "http://tickets:1235",
    "notifications_service_url": "http://notifications:1237",
    "inventory_service_url": "http://inventory:1238",
    "todos_service_url": "http://todos:1240",
    "time_tracking_service_url": "http://time_tracking:1241",
    "expenses_service_url": "http://expenses:1242",
    "projects_service_url": "http://projects:1243",
    "attendance_service_url": "http://attendance:1239",
    "vacation_service_url": "http://vacation:1244",
    "call_schedule_service_url": "http://call_schedule:1245",
    "chat_service_url": "http://chat:1246",
    "contacts_service_url": "http://contacts:1248",
    "correspondence_service_url": "http://correspondence:1249",
    "smart_home_service_url": "",
}


class Settings(BaseSettings):

    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    database_url: str = ""
    media_path: str = "./media"
    service_name: str = "gateway"

    sentry_dsn: str = ""
    gateway_base_url: str = ""
    auth_service_url: str = ""
    tickets_service_url: str = ""
    notifications_service_url: str = ""
    inventory_service_url: str = ""
    time_tracking_service_url: str = ""
    expenses_service_url: str = ""
    projects_service_url: str = ""
    attendance_service_url: str = ""
    vacation_service_url: str = ""
    attendance_hikvision_allowed_ips: str = ""
    todos_service_url: str = ""
    call_schedule_service_url: str = ""
    chat_service_url: str = ""
    contacts_service_url: str = ""
    correspondence_service_url: str = ""
    smart_home_service_url: str = ""
    frontend_url: str = ""

    cors_allow_private_network: bool = Field(
        default=False,
        validation_alias="CORS_ALLOW_PRIVATE_NETWORK",
    )

    ws_internal_secret: str = ""

    security_hsts_enabled: bool = Field(
        default=False,
        validation_alias="SECURITY_HSTS_ENABLED",
    )

    security_csp: str = ""

    attendance_range_snapshot_enabled: bool = Field(
        default=True,
        validation_alias="ATTENDANCE_RANGE_SNAPSHOT_ENABLED",
    )
    attendance_range_snapshot_refresh_sec: int = Field(
        default=600,
        validation_alias="ATTENDANCE_RANGE_SNAPSHOT_REFRESH_SEC",
        ge=60,
        le=86400,
    )

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"

    jwt_expire_minutes: int = Field(default=1440, validation_alias="JWT_EXPIRE_MINUTES")

    auth_session_cookie_name: str = "kl_access_token"
    auth_set_session_cookie: bool = Field(default=False, validation_alias="AUTH_SET_SESSION_COOKIE")
    auth_session_cookie_secure: bool = Field(default=True, validation_alias="AUTH_SESSION_COOKIE_SECURE")
    auth_session_cookie_samesite: str = Field(default="lax", validation_alias="AUTH_SESSION_COOKIE_SAMESITE")

    @field_validator(*tuple(_DEFAULT_SERVICE_URLS.keys()), mode="before")
    @classmethod
    def _default_microservice_urls_if_empty(cls, v: object, info: ValidationInfo) -> object:
        key = info.field_name or ""
        default = _DEFAULT_SERVICE_URLS.get(key)
        if default is None:
            return v
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        return v

    @field_validator("gateway_base_url", mode="after")
    @classmethod
    def _https_gateway_base_in_production(cls, v: str, info: ValidationInfo) -> str:
        url = (v or "").strip()
        if not url:
            return url
        env = str(info.data.get("environment") or "").strip().lower()
        if env == "production" and url.startswith("http://"):
            return "https://" + url[len("http://") :]
        return url

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
