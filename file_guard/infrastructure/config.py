from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "file_guard"
    database_url: str = "sqlite+aiosqlite:///./data/file_guard.db"
    files_dir: str = Field(default="./data/files", validation_alias="FILE_GUARD_FILES_DIR")
    auth_service_url: str = "http://auth:1236"
    notifications_service_url: str = "http://notifications:1237"
    ws_internal_secret: str = ""
    agent_token: str = Field(default="", validation_alias="FILE_GUARD_AGENT_TOKEN")
    notify_emails: str = Field(
        default="aakhmadjonov@kostalegal.com,zumerov@kostalegal.com",
        validation_alias="FILE_GUARD_NOTIFY_EMAILS",
    )
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, validation_alias="FILE_GUARD_MAX_UPLOAD_BYTES")
    max_stored_bytes: int = Field(default=2 * 1024 * 1024 * 1024, validation_alias="FILE_GUARD_MAX_STORED_BYTES")
    max_events_per_day: int = Field(default=2000, validation_alias="FILE_GUARD_MAX_EVENTS_PER_DAY")
    journal_logins: str = Field(default="aakhmadjonov,zumerov", validation_alias="FILE_GUARD_JOURNAL_LOGINS")
    auth_session_cookie_name: str = Field(default="kl_access_token", validation_alias="AUTH_SESSION_COOKIE_NAME")

    @field_validator("auth_service_url", "notifications_service_url", mode="before")
    @classmethod
    def _default_url_if_empty(cls, v: object, info) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            defaults = {
                "auth_service_url": "http://auth:1236",
                "notifications_service_url": "http://notifications:1237",
            }
            return defaults.get(info.field_name or "", v)
        return v

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
