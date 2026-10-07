from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "kosta_daily"
    auth_service_url: str = "http://auth:1236"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
