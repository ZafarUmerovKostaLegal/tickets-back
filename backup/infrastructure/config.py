from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "backup"
    backup_root: str = "/backups"
    media_path: str = "/media"
    backup_media: bool = True
    backup_schedule_cron: str = Field(
        default="0 3 * * *",
        validation_alias=AliasChoices("BACKUP_SCHEDULE_CRON", "backup_schedule_cron"),
    )
    backup_retention_days: int = Field(
        default=30,
        validation_alias=AliasChoices("BACKUP_RETENTION_DAYS", "backup_retention_days"),
    )
    backup_retention_min_count: int = Field(
        default=14,
        validation_alias=AliasChoices("BACKUP_RETENTION_MIN_COUNT", "backup_retention_min_count"),
    )
    backup_api_token: str = Field(
        default="",
        validation_alias=AliasChoices("BACKUP_API_TOKEN", "backup_api_token"),
    )
    backup_on_start: bool = Field(
        default=False,
        validation_alias=AliasChoices("BACKUP_ON_START", "backup_on_start"),
    )

    auth_database_url: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH_DATABASE_URL", "GATEWAY_DATABASE_URL"),
    )
    tickets_database_url: str = Field(default="", validation_alias="TICKETS_DATABASE_URL")
    notifications_database_url: str = Field(default="", validation_alias="NOTIFICATIONS_DATABASE_URL")
    inventory_database_url: str = Field(default="", validation_alias="INVENTORY_DATABASE_URL")
    attendance_database_url: str = Field(default="", validation_alias="ATTENDANCE_DATABASE_URL")
    todos_database_url: str = Field(default="", validation_alias="TODOS_DATABASE_URL")
    time_tracking_database_url: str = Field(default="", validation_alias="TIME_TRACKING_DATABASE_URL")
    expenses_database_url: str = Field(default="", validation_alias="EXPENSES_DATABASE_URL")
    vacation_database_url: str = Field(default="", validation_alias="VACATION_DATABASE_URL")
    chat_database_url: str = Field(default="", validation_alias="CHAT_DATABASE_URL")
    correspondence_database_url: str = Field(
        default="", validation_alias="CORRESPONDENCE_DATABASE_URL"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def database_targets(settings: Settings | None = None) -> list[tuple[str, str]]:
    s = settings or get_settings()
    pairs = [
        ("auth", s.auth_database_url),
        ("tickets", s.tickets_database_url),
        ("notifications", s.notifications_database_url),
        ("inventory", s.inventory_database_url),
        ("attendance", s.attendance_database_url),
        ("todos", s.todos_database_url),
        ("time_tracking", s.time_tracking_database_url),
        ("expenses", s.expenses_database_url),
        ("vacation", s.vacation_database_url),
        ("chat", s.chat_database_url),
        ("correspondence", s.correspondence_database_url),
    ]
    return [(name, url.strip()) for name, url in pairs if url.strip()]
