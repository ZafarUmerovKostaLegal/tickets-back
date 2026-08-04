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
    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL", "CORRESPONDENCE_DATABASE_URL"),
    )
    media_path: str = Field(default="/app/media", validation_alias=AliasChoices("MEDIA_PATH", "media_path"))
    service_name: str = "correspondence"
    auth_service_url: str = Field(default="http://auth:1236", validation_alias=AliasChoices("AUTH_SERVICE_URL"))
    max_file_bytes: int = Field(
        default=15 * 1024 * 1024,
        validation_alias=AliasChoices("CORRESPONDENCE_MAX_FILE_BYTES", "MAX_FILE_BYTES"),
    )
    notification_push_url: str = Field(
        default="http://gateway:1234/api/v1/notifications/system",
        validation_alias=AliasChoices("NOTIFICATION_PUSH_URL"),
    )
    ws_internal_secret: str = Field(
        default="",
        validation_alias=AliasChoices("WS_INTERNAL_SECRET"),
    )

    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url", mode="after")
    @classmethod
    def _database_url_non_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError(
                "Укажите DATABASE_URL или CORRESPONDENCE_DATABASE_URL (см. .env в корне репозитория)."
            )
        return v

    @field_validator("auth_service_url", mode="before")
    @classmethod
    def _default_auth_url_if_empty(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "http://auth:1236"
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
