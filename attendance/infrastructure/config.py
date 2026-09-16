from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = ""
    service_name: str = "attendance"
    media_path: str = "/app/media"
    max_explanation_photo_size_mb: int = 10
    hikvision_device_ip: str = ""
    hikvision_device_port: int = 80
    hikvision_device_user: str = "admin"
    hikvision_device_password: str = ""
    hikvision_request_timeout: float = 60.0
    hikvision_device_ips: str = ""

    # Persist camera AcsEvent history + keep it fresh.
    attendance_ingest_enabled: bool = True
    attendance_ingest_interval_sec: int = 300
    attendance_ingest_lookback_minutes: int = 180
    attendance_ingest_max_records_per_device: int = 10000
    # Optional auto backfill on boot (e.g. 2026-01-01). Empty = skip.
    attendance_backfill_from: str = ""
    attendance_backfill_to: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
