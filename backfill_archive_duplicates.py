"""Разовый бэкфилл: авто-архив дубликатов по всем уже подтверждённым отчётам.

Гасит (void + снимок в time_tracking_entry_archives) дубли записей за периоды
полностью подтверждённых партнёрских отчётов — ровно те строки, которые просмотр
отчёта схлопывает лишь на экране. После этого бюджет/итоги/«не выставлено»/счета
перестают учитывать лишние копии. Действие ОБРАТИМО: вкладка «Дубликаты» →
«Восстановить» (или восстановление из архива по archive_id).

Запуск (PowerShell), из папки tickets-back:

    $env:PYTHONPATH = "$PWD;$PWD/time_tracking"
    $env:PYTHONIOENCODING = "utf-8"
    # Сначала пробный прогон (ничего не пишет, только показывает, что будет заархивировано):
    python backfill_archive_duplicates.py
    # Затем реальное применение:
    python backfill_archive_duplicates.py --apply

Опции:
    --apply                 реально применить (по умолчанию — dry-run, только отчёт)
    --project <PROJECT_ID>  ограничить одним проектом
    --archived-by <ID>      кто числится инициатором архивации (по умолчанию 0 = система)
"""

from __future__ import annotations

import argparse
import asyncio

from application.entry_archive_service import (
    AUTO_ARCHIVE_SYSTEM_USER_ID,
    auto_archive_duplicates_for_project_period,
)
from infrastructure.database import async_session_factory
from infrastructure.report_cache import invalidate_all_reports
from infrastructure.repository_partner_report_confirmations import (
    PartnerReportConfirmationRepository,
)


async def run(*, apply: bool, project_filter: str | None, archived_by: int) -> None:
    total_requests = 0
    processed = 0
    total_archived = 0
    total_skipped = 0
    total_groups = 0
    touched_projects: set[str] = set()

    async with async_session_factory() as session:
        conf_repo = PartnerReportConfirmationRepository(session)
        rows = await conf_repo.list_all_fully_confirmed()
        total_requests = len(rows)

        for m in rows:
            pid = (m.project_id or "").strip()
            if project_filter and pid != project_filter.strip():
                continue
            processed += 1
            result = await auto_archive_duplicates_for_project_period(
                session,
                project_id=pid,
                date_from=m.date_from,
                date_to=m.date_to,
                archived_by_auth_user_id=archived_by,
                commit=False,
            )
            arch = int(result.get("archived_count", 0))
            skip = int(result.get("skipped_count", 0))
            grp = int(result.get("group_count", 0))
            total_archived += arch
            total_skipped += skip
            total_groups += grp

            if arch or skip:
                touched_projects.add(pid)
                print(
                    f"[{'APPLY' if apply else 'DRY '}] project={pid} "
                    f"{m.date_from.isoformat()}..{m.date_to.isoformat()} "
                    f"groups={grp} archived={arch} skipped(on_invoice)={skip}"
                )

            if apply and (arch or skip):
                await session.commit()
            else:
                # dry-run или нечего писать — откатываем накопленные в сессии изменения.
                await session.rollback()

        if apply and total_archived:
            invalidate_all_reports()

    print("-" * 60)
    print(f"Подтверждённых отчётов всего:      {total_requests}")
    print(f"Обработано (после фильтра проекта): {processed}")
    print(f"Групп дублей найдено:              {total_groups}")
    print(f"Записей {'заархивировано' if apply else 'к архивации'}:          {total_archived}")
    print(f"Пропущено (в активном счёте):      {total_skipped}")
    print(f"Затронуто проектов:               {len(touched_projects)}")
    if not apply:
        print("\nЭто был ПРОБНЫЙ прогон (--apply не указан). Ничего не изменено.")
        print("Для применения запустите: python backfill_archive_duplicates.py --apply")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill: auto-archive duplicates for confirmed reports")
    parser.add_argument("--apply", action="store_true", help="реально применить (по умолчанию dry-run)")
    parser.add_argument("--project", default=None, help="ограничить одним project_id")
    parser.add_argument(
        "--archived-by",
        type=int,
        default=AUTO_ARCHIVE_SYSTEM_USER_ID,
        help="auth_user_id инициатора архивации (по умолчанию 0 = система)",
    )
    args = parser.parse_args()
    asyncio.run(
        run(apply=args.apply, project_filter=args.project, archived_by=args.archived_by)
    )


if __name__ == "__main__":
    main()
