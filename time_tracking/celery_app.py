from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

from backend_common.redis_url_warn import warn_if_redis_url_unauthenticated

REDIS = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
warn_if_redis_url_unauthenticated(REDIS, service="time_tracking_celery")
app = Celery(
    "time_tracking",
    broker=REDIS,
    backend=REDIS,
    include=["celery_tasks.weekly_report"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=os.environ.get("WEEKLY_SUBMIT_TZ", "Asia/Tashkent"),
    enable_utc=True,
)

H = int(os.environ.get("WEEKLY_SUBMIT_HOUR", "12"))
M = int(os.environ.get("WEEKLY_SUBMIT_MINUTE", "0"))

_dow = os.environ.get("WEEKLY_SUBMIT_DOW", "1")
try:
    DOW: int | str = int(_dow)
except ValueError:
    DOW = _dow

app.conf.beat_schedule = {
    "weekly-time-submit": {
        "task": "tt.weekly.submit_last_closed_iso_weeks",
        "schedule": crontab(hour=H, minute=M, day_of_week=DOW),
    },
}
