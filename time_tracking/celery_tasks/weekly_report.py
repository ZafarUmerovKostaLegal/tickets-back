

from __future__ import annotations

import logging

from celery import shared_task

_log = logging.getLogger(__name__)


@shared_task(name="tt.weekly.submit_last_closed_iso_weeks", ignore_result=True)
def submit_last_closed_iso_weeks() -> int:
    from application.weekly_submission_service import run_weekly_auto_submit_sync

    n = run_weekly_auto_submit_sync()

    _log.info(
        "[weekly-celery] auto-submit users_with_new_submission=%s (0 = all weeks already closed or no users)",
        n,
    )
    return n
