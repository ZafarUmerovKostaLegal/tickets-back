import logging
from functools import lru_cache
from urllib.parse import quote, unquote, urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings

_log = logging.getLogger("chat.config")

# Compose default baked into DATABASE_URL. chat_db itself is created with CHAT_DB_PASSWORD.
_COMPOSE_DEFAULT_DB_PASSWORD = "chat"


class Settings(BaseSettings):
    database_url: str = ""
    chat_database_url: str = ""
    chat_db_user: str = "chat"
    chat_db_password: str = "chat"
    chat_db_host: str = "chat_db"
    chat_db_port: int = 5432
    chat_db_name: str = "kosta_chat"
    service_name: str = "chat"
    auth_service_url: str = ""
    chat_push_url: str = ""
    ws_internal_secret: str = ""
    chat_vapid_public_key: str = ""
    chat_vapid_private_key: str = ""
    chat_vapid_subject: str = "mailto:notifications@kostalegal.com"
    max_message_length: int = 4000
    media_path: str = "/app/media"
    max_file_bytes: int = 15 * 1024 * 1024

    @field_validator("auth_service_url", mode="before")
    @classmethod
    def _default_auth_url_if_empty(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "http://auth:1236"
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}


def build_database_url_from_parts(settings: Settings) -> str:
    user = quote((settings.chat_db_user or "chat").strip() or "chat", safe="")
    password = quote((settings.chat_db_password or "").strip(), safe="")
    host = (settings.chat_db_host or "chat_db").strip() or "chat_db"
    port = int(settings.chat_db_port or 5432)
    name = (settings.chat_db_name or "kosta_chat").strip() or "kosta_chat"
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def _url_password(url: str) -> str:
    try:
        return unquote(urlparse(url).password or "")
    except Exception:
        return ""


def resolve_database_url(settings: Settings) -> str:
    """Use CHAT_DB_PASSWORD when the compose URL is still the default chat/chat.

    chat_db is initialized from CHAT_DB_PASSWORD. The service URL used to stay
    postgresql://chat:chat@... unless CHAT_DATABASE_URL was set too. Postgres then
    rejects the login, the process exits, and the gateway cannot resolve `chat`.
    """
    explicit = (settings.chat_database_url or settings.database_url or "").strip()
    parts = build_database_url_from_parts(settings)
    if not explicit:
        return parts
    configured = (settings.chat_db_password or "").strip()
    if (
        _url_password(explicit) == _COMPOSE_DEFAULT_DB_PASSWORD
        and configured
        and configured != _COMPOSE_DEFAULT_DB_PASSWORD
    ):
        _log.warning(
            "chat DATABASE_URL still uses the default password; connecting with CHAT_DB_PASSWORD instead"
        )
        return parts
    return explicit


@lru_cache
def get_settings() -> Settings:
    return Settings()
