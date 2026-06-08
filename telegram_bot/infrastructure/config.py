from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "telegram_bot"
    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
