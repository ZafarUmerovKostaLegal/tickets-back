from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "file_manager"
    auth_service_url: str = "http://auth:1236"
    auth_session_cookie_name: str = "kl_access_token"
    database_url: str = "sqlite+aiosqlite:///./data/file_manager.db"
    time_tracking_service_url: str = Field(
        default="http://time_tracking:1241",
        validation_alias=AliasChoices("TIME_TRACKING_SERVICE_URL", "time_tracking_service_url"),
    )
    max_file_bytes: int = Field(
        default=50 * 1024 * 1024,
        validation_alias=AliasChoices("FILE_MANAGER_MAX_FILE_BYTES", "max_file_bytes"),
    )
    # Large files go through resumable chunked uploads and are stored on disk, not in the database.
    blob_dir: str = Field(
        default="./data/blobs",
        validation_alias=AliasChoices("FILE_MANAGER_BLOB_DIR", "blob_dir"),
    )
    max_upload_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        validation_alias=AliasChoices("FILE_MANAGER_MAX_UPLOAD_BYTES", "max_upload_bytes"),
    )
    upload_chunk_bytes: int = Field(
        default=8 * 1024 * 1024,
        validation_alias=AliasChoices("FILE_MANAGER_UPLOAD_CHUNK_BYTES", "upload_chunk_bytes"),
    )
    # Keep this much disk free for the database and other files.
    disk_reserve_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        validation_alias=AliasChoices("FILE_MANAGER_DISK_RESERVE_BYTES", "disk_reserve_bytes"),
    )
    upload_session_ttl_hours: int = Field(
        default=48,
        validation_alias=AliasChoices("FILE_MANAGER_UPLOAD_TTL_HOURS", "upload_session_ttl_hours"),
    )
    # Server-side auto-filing of mail (Microsoft Graph, application permission Mail.Read + admin consent).
    mail_autofile_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("FILE_MANAGER_MAIL_AUTOFILE", "mail_autofile_enabled"),
    )
    mail_autofile_interval_sec: int = Field(
        default=300,
        validation_alias=AliasChoices("FILE_MANAGER_MAIL_AUTOFILE_INTERVAL_SEC", "mail_autofile_interval_sec"),
    )
    # Teams meeting recordings → matter folders (same Azure app, Teams permissions + access policy).
    meeting_recordings_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("FILE_MANAGER_MEETING_RECORDINGS", "meeting_recordings_enabled"),
    )
    meeting_recordings_interval_sec: int = Field(
        default=300,
        validation_alias=AliasChoices("FILE_MANAGER_MEETING_RECORDINGS_INTERVAL_SEC", "meeting_recordings_interval_sec"),
    )
    # A linked meeting is watched this long after it was linked or last produced a file.
    meeting_watch_days: int = Field(
        default=45,
        validation_alias=AliasChoices("FILE_MANAGER_MEETING_WATCH_DAYS", "meeting_watch_days"),
    )
    graph_tenant_id: str = Field(default="", validation_alias=AliasChoices("MICROSOFT_TENANT_ID", "AZURE_TENANT_ID"))
    graph_client_id: str = Field(default="", validation_alias=AliasChoices("MICROSOFT_CLIENT_ID", "AZURE_CLIENT_ID"))
    graph_client_secret: str = Field(default="", validation_alias=AliasChoices("MICROSOFT_CLIENT_SECRET", "AZURE_CLIENT_SECRET"))

    def graph_ready(self) -> bool:
        return bool(self.graph_tenant_id.strip() and self.graph_client_id.strip() and self.graph_client_secret.strip())

    @field_validator("auth_service_url", "time_tracking_service_url", mode="before")
    @classmethod
    def _default_url_if_empty(cls, v: object, info) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return {"auth_service_url": "http://auth:1236"}.get(info.field_name, "http://time_tracking:1241")
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
