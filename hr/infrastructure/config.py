from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "hr"
    auth_service_url: str = "http://auth:1236"
    auth_session_cookie_name: str = "kl_access_token"
    database_url: str = "sqlite+aiosqlite:///./data/hr.db"

    @field_validator("auth_service_url", mode="before")
    @classmethod
    def _default_url_if_empty(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "http://auth:1236"
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
