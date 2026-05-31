from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "contacts"
    auth_service_url: str = "http://auth:1236"
    time_tracking_service_url: str = "http://time_tracking:1241"

    @field_validator("auth_service_url", "time_tracking_service_url", mode="before")
    @classmethod
    def _default_url_if_empty(cls, v: object, info) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            defaults = {
                "auth_service_url": "http://auth:1236",
                "time_tracking_service_url": "http://time_tracking:1241",
            }
            return defaults.get(info.field_name or "", v)
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
