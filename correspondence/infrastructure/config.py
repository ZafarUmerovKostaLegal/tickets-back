from functools import lru_cache
from pathlib import Path
import os

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVICE_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SERVICE_DIR.parent


def _env_files() -> tuple[str, ...]:
    paths: list[str] = []
    for p in (_SERVICE_DIR / ".env", _REPO_ROOT / ".env"):
        if p.is_file():
            paths.append(str(p))
    return tuple(paths) if paths else (".env",)


def _env_first(*names: str) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


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
    notifications_service_url: str = Field(
        default="http://notifications:1237",
        validation_alias=AliasChoices("NOTIFICATIONS_SERVICE_URL"),
    )
    ws_internal_secret: str = Field(
        default="",
        validation_alias=AliasChoices("WS_INTERNAL_SECRET"),
    )
    # Prefer CORRESPONDENCE_*; also accept EXPENSE_* / SMTP_* (same mailbox as expenses).
    smtp_host: str = Field(
        default="",
        validation_alias=AliasChoices("CORRESPONDENCE_SMTP_HOST", "EXPENSE_SMTP_HOST", "SMTP_HOST"),
    )
    smtp_port: int = Field(
        default=587,
        validation_alias=AliasChoices("CORRESPONDENCE_SMTP_PORT", "EXPENSE_SMTP_PORT", "SMTP_PORT"),
    )
    smtp_user: str = Field(
        default="",
        validation_alias=AliasChoices("CORRESPONDENCE_SMTP_USER", "EXPENSE_SMTP_USER", "SMTP_USER"),
    )
    smtp_password: str = Field(
        default="",
        validation_alias=AliasChoices("CORRESPONDENCE_SMTP_PASSWORD", "EXPENSE_SMTP_PASSWORD", "SMTP_PASSWORD"),
    )
    smtp_use_tls: bool = Field(
        default=True,
        validation_alias=AliasChoices("CORRESPONDENCE_SMTP_USE_TLS", "EXPENSE_SMTP_USE_TLS", "SMTP_USE_TLS"),
    )
    mail_from: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CORRESPONDENCE_MAIL_FROM",
            "EXPENSE_MAIL_FROM",
            "EXPENSE_SMTP_FROM",
            "SMTP_FROM",
        ),
    )
    public_app_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CORRESPONDENCE_PUBLIC_APP_URL",
            "PUBLIC_APP_URL",
            "FRONTEND_URL",
            "GATEWAY_PUBLIC_URL",
        ),
    )
    public_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GATEWAY_BASE_URL",
            "PUBLIC_API_BASE_URL",
            "CORRESPONDENCE_PUBLIC_API_BASE_URL",
        ),
    )
    correspondence_download_token_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CORRESPONDENCE_DOWNLOAD_TOKEN_SECRET",
            "EXPENSE_EMAIL_ACTION_SECRET",
        ),
    )
    correspondence_download_token_ttl_seconds: int = Field(
        default=604800,  # 7 days
        ge=60,
        le=2592000,
        validation_alias=AliasChoices("CORRESPONDENCE_DOWNLOAD_TOKEN_TTL_SECONDS"),
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

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _smtp_port_empty_to_default(cls, v: object) -> object:
        if v == "" or v is None:
            return 587
        return v

    @field_validator("smtp_host", "smtp_user", "mail_from", "public_app_url", mode="after")
    @classmethod
    def _strip_smtp_identity(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("smtp_password", mode="after")
    @classmethod
    def _normalize_smtp_password(cls, v: str) -> str:
        raw = (v or "").strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            return raw[1:-1].strip()
        return raw

    @field_validator("smtp_use_tls", mode="before")
    @classmethod
    def _empty_env_bool_as_default(cls, v: object) -> object:
        if v == "":
            return True
        return v

    @model_validator(mode="after")
    def _fill_empty_smtp_from_expense_env(self) -> "Settings":
        """Portainer/Compose often sets CORRESPONDENCE_SMTP_*='' and breaks nested ${EXPENSE_*}."""
        if not self.smtp_host:
            object.__setattr__(
                self,
                "smtp_host",
                _env_first("EXPENSE_SMTP_HOST", "SMTP_HOST"),
            )
        if not self.smtp_user:
            object.__setattr__(
                self,
                "smtp_user",
                _env_first("EXPENSE_SMTP_USER", "SMTP_USER"),
            )
        if not self.smtp_password:
            object.__setattr__(
                self,
                "smtp_password",
                _env_first("EXPENSE_SMTP_PASSWORD", "SMTP_PASSWORD"),
            )
        if not self.mail_from:
            object.__setattr__(
                self,
                "mail_from",
                _env_first("EXPENSE_MAIL_FROM", "EXPENSE_SMTP_FROM", "SMTP_FROM"),
            )
        if not self.public_app_url:
            object.__setattr__(
                self,
                "public_app_url",
                _env_first("PUBLIC_APP_URL", "FRONTEND_URL", "GATEWAY_PUBLIC_URL"),
            )
        # If correspondence port was left empty (defaulted to 587), prefer EXPENSE_SMTP_PORT.
        corr_port_raw = (os.environ.get("CORRESPONDENCE_SMTP_PORT") or "").strip()
        expense_port_raw = (os.environ.get("EXPENSE_SMTP_PORT") or os.environ.get("SMTP_PORT") or "").strip()
        if not corr_port_raw and expense_port_raw.isdigit():
            object.__setattr__(self, "smtp_port", int(expense_port_raw))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
