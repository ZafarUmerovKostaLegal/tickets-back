from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "file_manager"
    auth_service_url: str = "http://auth:1236"
    auth_session_cookie_name: str = "kl_access_token"
    database_url: str = "sqlite+aiosqlite:///./data/file_manager.db"
    time_tracking_service_url: str = Field(
        default="http://time_tracking:1241",
        validation_alias=AliasChoices("TIME_TRACKING_SERVICE_URL", "time_tracking_service_url"),
    )
    max_file_bytes: int = Field(
        default=50 * 1024 * 1024,
        validation_alias=AliasChoices("FILE_MANAGER_MAX_FILE_BYTES", "max_file_bytes"),
    )

    @field_validator("auth_service_url", "time_tracking_service_url", mode="before")
    @classmethod
    def _default_url_if_empty(cls, v: object, info) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return {"auth_service_url": "http://auth:1236"}.get(info.field_name, "http://time_tracking:1241")
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
