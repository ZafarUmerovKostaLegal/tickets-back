from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = ""
    service_name: str = "time_tracking"
    expenses_service_url: str = "http://expenses:1242"

    auth_service_url: str = ""

    redis_url: str = "redis://localhost:6379/0"

    smtp_host: str = Field(
        default="",
        validation_alias=AliasChoices("TT_SMTP_HOST", "EXPENSE_SMTP_HOST", "SMTP_HOST"),
    )
    smtp_port: int = Field(
        default=587,
        validation_alias=AliasChoices("TT_SMTP_PORT", "EXPENSE_SMTP_PORT", "SMTP_PORT"),
    )
    smtp_user: str = Field(
        default="",
        validation_alias=AliasChoices("TT_SMTP_USER", "EXPENSE_SMTP_USER", "SMTP_USER"),
    )
    smtp_password: str = Field(
        default="",
        validation_alias=AliasChoices("TT_SMTP_PASSWORD", "EXPENSE_SMTP_PASSWORD", "SMTP_PASSWORD"),
    )
    smtp_use_tls: bool = Field(
        default=True,
        validation_alias=AliasChoices("TT_SMTP_USE_TLS", "EXPENSE_SMTP_USE_TLS", "SMTP_USE_TLS"),
    )
    mail_from: str = Field(
        default="",
        validation_alias=AliasChoices("TT_MAIL_FROM", "EXPENSE_MAIL_FROM", "MAIL_FROM"),
    )
    notify_project_access_added: bool = Field(
        default=True,
        validation_alias="TT_NOTIFY_PROJECT_ACCESS_ADDED",
    )
    project_access_mail_signature_name: str = Field(
        default="Гузаль Темирова",
        validation_alias="TT_PROJECT_ACCESS_MAIL_SIGNATURE_NAME",
    )
    project_access_mail_signature_title: str = Field(
        default="Контрактный менеджер",
        validation_alias="TT_PROJECT_ACCESS_MAIL_SIGNATURE_TITLE",
    )
    notify_invoice_sent_accounting: bool = Field(
        default=True,
        validation_alias="TT_NOTIFY_INVOICE_SENT_ACCOUNTING",
    )
    invoice_sent_notify_to: str = Field(
        default="oidrisova@kostalegal.com",
        validation_alias="TT_INVOICE_SENT_NOTIFY_TO",
    )

    model_config = {"env_file": ".env", "extra": "ignore"}

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _smtp_port_empty_to_default(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return 587
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
