

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "call_schedule"
    auth_service_url: str = "http://auth:1236"

    auth_session_cookie_name: str = Field(default="kl_access_token")


    call_schedule_mailbox: str = "info@kostalegal.com"


    microsoft_tenant_id: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""


    call_schedule_microsoft_tenant_id: str = ""
    call_schedule_microsoft_client_id: str = ""
    call_schedule_microsoft_client_secret: str = ""


    call_schedule_create_as_teams_meeting: bool = True

    call_schedule_online_meeting_provider: str = "teamsForBusiness"

    call_schedule_prefer_zoom_join_over_teams: bool = True

    @field_validator("auth_service_url", mode="before")
    @classmethod
    def _auth_url(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return "http://auth:1236"
        return v

    @field_validator(
        "call_schedule_mailbox",
        "microsoft_tenant_id",
        "microsoft_client_id",
        "microsoft_client_secret",
        "call_schedule_microsoft_tenant_id",
        "call_schedule_microsoft_client_id",
        "call_schedule_microsoft_client_secret",
        "auth_session_cookie_name",
        "call_schedule_online_meeting_provider",
        mode="before",
    )
    @classmethod
    def _strip(cls, v: object) -> str:
        return (v or "").strip() if v is not None else ""

    def graph_client_credentials(self) -> tuple[str, str, str]:

        if self.call_schedule_microsoft_client_id:
            return (
                self.call_schedule_microsoft_tenant_id or self.microsoft_tenant_id,
                self.call_schedule_microsoft_client_id,
                self.call_schedule_microsoft_client_secret,
            )
        return self.microsoft_tenant_id, self.microsoft_client_id, self.microsoft_client_secret

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
