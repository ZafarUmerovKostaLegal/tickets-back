from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = ""
    service_name: str = "chat"
    auth_service_url: str = ""
    chat_push_url: str = ""
    ws_internal_secret: str = ""
    max_message_length: int = 4000
    media_path: str = "/app/media"
    max_file_bytes: int = 15 * 1024 * 1024

    @field_validator("auth_service_url", mode="before")
    @classmethod
    def _default_auth_url_if_empty(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "http://auth:1236"
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
