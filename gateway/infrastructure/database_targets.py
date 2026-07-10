from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class DatabaseMonitorSettings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    auth_database_url: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH_DATABASE_URL", "GATEWAY_DATABASE_URL", "DATABASE_URL"),
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
    correspondence_database_url: str = Field(default="", validation_alias="CORRESPONDENCE_DATABASE_URL")
    redis_url: str = Field(default="", validation_alias="REDIS_URL")


def database_targets(settings: DatabaseMonitorSettings | None = None) -> list[tuple[str, str]]:
    s = settings or DatabaseMonitorSettings()
    pairs = [
        ("users", s.auth_database_url),
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
