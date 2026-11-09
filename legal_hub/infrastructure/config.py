from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "legal_hub"
    auth_service_url: str = "http://auth:1236"
    auth_session_cookie_name: str = "kl_access_token"
    database_url: str = "sqlite+aiosqlite:///./data/legal_hub.db"
    time_tracking_service_url: str = Field(
        default="http://time_tracking:1241",
        validation_alias=AliasChoices("TIME_TRACKING_SERVICE_URL", "time_tracking_service_url"),
    )
    public_app_url: str = Field(default="https://tickets.kostalegal.com", validation_alias="LEGAL_HUB_PUBLIC_APP_URL")
    smtp_host: str = Field(default="", validation_alias=AliasChoices("LEGAL_HUB_SMTP_HOST", "EXPENSE_SMTP_HOST", "SMTP_HOST"))
    smtp_port: int = Field(default=587, validation_alias=AliasChoices("LEGAL_HUB_SMTP_PORT", "EXPENSE_SMTP_PORT", "SMTP_PORT"))
    smtp_user: str = Field(default="", validation_alias=AliasChoices("LEGAL_HUB_SMTP_USER", "EXPENSE_SMTP_USER", "SMTP_USER"))
    smtp_password: str = Field(default="", validation_alias=AliasChoices("LEGAL_HUB_SMTP_PASSWORD", "EXPENSE_SMTP_PASSWORD", "SMTP_PASSWORD"))
    smtp_use_tls: bool = Field(default=True, validation_alias=AliasChoices("LEGAL_HUB_SMTP_USE_TLS", "EXPENSE_SMTP_USE_TLS", "SMTP_USE_TLS"))
    mail_from: str = Field(default="", validation_alias=AliasChoices("LEGAL_HUB_MAIL_FROM", "EXPENSE_MAIL_FROM", "SMTP_FROM"))

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
