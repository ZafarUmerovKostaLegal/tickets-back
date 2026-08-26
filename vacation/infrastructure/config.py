

import logging
from functools import lru_cache
from urllib.parse import quote

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger("vacation.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_URL", "VACATION_DATABASE_URL"),
    )

    vacation_use_explicit_database_url: bool = Field(
        default=False,
        validation_alias="VACATION_USE_EXPLICIT_DATABASE_URL",
    )
    vacation_db_user: str = Field(default="vacation", validation_alias="VACATION_DB_USER")
    vacation_db_password: str = Field(default="vacation", validation_alias="VACATION_DB_PASSWORD")
    vacation_db_host: str = Field(default="vacation_db", validation_alias="VACATION_DB_HOST")
    vacation_db_port: int = Field(default=5432, validation_alias="VACATION_DB_PORT")
    vacation_db_name: str = Field(default="kosta_vacation", validation_alias="VACATION_DB_NAME")
    service_name: str = "vacation"

    auth_service_url: str = Field(default="http://auth:1236", validation_alias="AUTH_SERVICE_URL")
    media_path: str = Field(default="/app/media", validation_alias="MEDIA_PATH")
    frontend_url: str = Field(default="", validation_alias="FRONTEND_URL")
    public_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GATEWAY_BASE_URL",
            "PUBLIC_API_BASE_URL",
            "VACATION_PUBLIC_API_BASE_URL",
        ),
    )

    smtp_host: str = Field(default="", validation_alias="VACATION_SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="VACATION_SMTP_PORT")
    smtp_user: str = Field(default="", validation_alias="VACATION_SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="VACATION_SMTP_PASSWORD")
    smtp_use_tls: bool = Field(default=True, validation_alias="VACATION_SMTP_USE_TLS")
    mail_from: str = Field(default="", validation_alias="VACATION_MAIL_FROM")
    mail_bcc: str = Field(default="", validation_alias="VACATION_MAIL_BCC")

    # Управляющий партнёр: обязательная вторая ступень согласования заявок и
    # адресат заявления в PDF.
    managing_partner_email: str = Field(
        default="aakhmadjonov@kostalegal.com",
        validation_alias="VACATION_MANAGING_PARTNER_EMAIL",
    )
    managing_partner_name: str = Field(
        default="Azizbek Akhmadjonov",
        validation_alias="VACATION_MANAGING_PARTNER_NAME",
    )

    email_action_secret: str = Field(default="", validation_alias="VACATION_EMAIL_ACTION_SECRET")
    email_action_ttl_seconds: int = Field(default=14 * 24 * 3600, validation_alias="VACATION_EMAIL_ACTION_TTL_SECONDS")
    email_action_confirm_step: bool = Field(default=True, validation_alias="VACATION_EMAIL_ACTION_CONFIRM_STEP")

    # Annual paid leave entitlement (calendar days) and mandatory continuous portion.
    annual_entitled_days: int = Field(default=28, validation_alias="VACATION_ANNUAL_ENTITLED_DAYS")
    min_continuous_vacation_days: int = Field(
        default=14,
        validation_alias="VACATION_MIN_CONTINUOUS_DAYS",
    )
    # Short annual parts allowed before the continuous block (1+2+3… up to this total).
    flexible_annual_days: int = Field(
        default=7,
        validation_alias="VACATION_FLEXIBLE_ANNUAL_DAYS",
    )

    @field_validator("vacation_db_password", "vacation_db_user", "vacation_db_name", mode="before")
    @classmethod
    def strip_bom_and_edges(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().replace("\ufeff", "")
        return v


def build_database_url_from_parts(settings: Settings) -> str:

    u = quote((settings.vacation_db_user or "vacation").strip() or "vacation", safe="")
    p = quote((settings.vacation_db_password or "").strip(), safe="")
    h = (settings.vacation_db_host or "vacation_db").strip() or "vacation_db"
    port = int(settings.vacation_db_port or 5432)
    n = (settings.vacation_db_name or "kosta_vacation").strip() or "kosta_vacation"
    return f"postgresql://{u}:{p}@{h}:{port}/{n}"


def resolve_database_url(settings: Settings) -> str:
    raw = (settings.database_url or "").strip()

    if raw and settings.vacation_use_explicit_database_url:
        return raw
    if raw and not settings.vacation_use_explicit_database_url:
        _log.warning(
            "Задан DATABASE_URL или VACATION_DATABASE_URL, но VACATION_USE_EXPLICIT_DATABASE_URL не true — "
            "подключение идёт из VACATION_DB_* (пароль из VACATION_DB_PASSWORD). Удалите лишний URL из env, чтобы не путаться."
        )
    return build_database_url_from_parts(settings)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_managing_partner_email(email: str | None) -> bool:
    """Совпадает ли адрес с управляющим партнёром (вторая ступень / адресат PDF)."""
    configured = (get_settings().managing_partner_email or "").strip().casefold()
    mine = (email or "").strip().casefold()
    return bool(configured and mine and configured == mine)
