from dataclasses import dataclass
from datetime import datetime, time


@dataclass
class HealthEntity:
    status: str
    service: str
    timestamp: datetime


@dataclass
class WorkdaySettings:
    workday_start: time
    workday_end: time
    late_threshold_minutes: int
    daily_hours_norm: int
