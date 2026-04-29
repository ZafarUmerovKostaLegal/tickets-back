from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = ""
    auth_service_url: str = ""
    media_path: str = "./media"
    service_name: str = "notifications"
    max_photo_size_mb: int = 10
    ws_internal_secret: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
