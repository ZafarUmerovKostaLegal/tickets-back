from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = ""
    service_name: str = "todos"
    media_path: str = "./media"
    max_upload_mb: int = 15

    auth_service_url: str = ""

    @field_validator("auth_service_url", mode="before")
    @classmethod
    def _default_auth_url_if_empty(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "http://auth:1236"
        return v

    microsoft_client_id: str = ""
    microsoft_tenant_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_oauth_state_secret: str = ""
    microsoft_redirect_uri: str = ""

    calendar_connected_redirect_url: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    @field_validator(
        "microsoft_client_id",
        "microsoft_tenant_id",
        "microsoft_client_secret",
        "microsoft_oauth_state_secret",
        "calendar_connected_redirect_url",
        mode="before",
    )
    @classmethod
    def strip_str(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("microsoft_redirect_uri", mode="before")
    @classmethod
    def normalize_redirect_uri(cls, v: str) -> str:

        parts = (v or "").strip().replace("\n", "").replace("\r", "").split()
        return parts[0] if parts else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
