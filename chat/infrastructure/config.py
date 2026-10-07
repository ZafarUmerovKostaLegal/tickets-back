from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVICE_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SERVICE_DIR.parent


def _env_files() -> tuple[str, ...]:
    paths: list[str] = []
    for p in (_SERVICE_DIR / ".env", _REPO_ROOT / ".env"):
        if p.is_file():
            paths.append(str(p))
    return tuple(paths) if paths else (".env",)


class Settings(BaseSettings):
    """DB URL is optional at process start so the container can stay up without Postgres."""

    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL", "CHAT_DATABASE_URL"),
    )
    service_name: str = "chat"
    auth_service_url: str = Field(
        default="http://auth:1236",
        validation_alias=AliasChoices("AUTH_SERVICE_URL"),
    )
    chat_push_url: str = Field(
        default="",
        validation_alias=AliasChoices("CHAT_PUSH_URL"),
    )
    ws_internal_secret: str = Field(
        default="",
        validation_alias=AliasChoices("WS_INTERNAL_SECRET"),
    )
    chat_vapid_public_key: str = Field(
        default="",
        validation_alias=AliasChoices("CHAT_VAPID_PUBLIC_KEY"),
    )
    chat_vapid_private_key: str = Field(
        default="",
        validation_alias=AliasChoices("CHAT_VAPID_PRIVATE_KEY"),
    )
    chat_vapid_subject: str = Field(
        default="mailto:notifications@kostalegal.com",
        validation_alias=AliasChoices("CHAT_VAPID_SUBJECT"),
    )
    max_message_length: int = 4000
    media_path: str = Field(
        default="/app/media",
        validation_alias=AliasChoices("MEDIA_PATH"),
    )
    max_file_bytes: int = Field(
        default=15 * 1024 * 1024,
        validation_alias=AliasChoices("CHAT_MAX_FILE_BYTES", "MAX_FILE_BYTES"),
    )
    # When true, skip DB init entirely (HTTP-only). Set CHAT_DISABLE_DB=1 to test networking.
    chat_disable_db: bool = Field(
        default=False,
        validation_alias=AliasChoices("CHAT_DISABLE_DB"),
    )

    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("auth_service_url", mode="before")
    @classmethod
    def _default_auth_url_if_empty(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "http://auth:1236"
        return v

    @field_validator("chat_disable_db", mode="before")
    @classmethod
    def _parse_disable_db(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
