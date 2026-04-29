from datetime import datetime, time
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


class WorkdaySettingsResponse(BaseModel):
    workday_start: time
    workday_end: time
    late_threshold_minutes: int
    daily_hours_norm: int


class WorkdaySettingsUpdateRequest(BaseModel):
    workday_start: time | None = None
    workday_end: time | None = None
    late_threshold_minutes: int | None = None
    daily_hours_norm: int | None = None
