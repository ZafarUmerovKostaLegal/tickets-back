from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from application.backup_runner import run_backup
from infrastructure.config import get_settings

_log = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


def _run_backup_job() -> None:
    settings = get_settings()
    _log.info("scheduled backup started")
    try:
        manifest = run_backup(settings=settings)
        _log.info("scheduled backup finished: %s status=%s", manifest.id, manifest.status)
    except Exception:
        _log.exception("scheduled backup failed")


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _run_backup_job,
        CronTrigger.from_crontab(settings.backup_schedule_cron),
        id="daily_backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    _log.info("backup scheduler started cron=%s", settings.backup_schedule_cron)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def maybe_run_on_start() -> None:
    settings = get_settings()
    if not settings.backup_on_start:
        return
    _log.info("BACKUP_ON_START=true — running initial backup")
    await asyncio.to_thread(run_backup, settings=settings)
