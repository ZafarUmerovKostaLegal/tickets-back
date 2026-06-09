"""Replace task "Drafting Documents" with "Drafting" in all projects.

Dry-run by default. Use --execute to apply changes.

Examples:
  python scripts/merge_drafting_documents_task.py
  python scripts/merge_drafting_documents_task.py --execute
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TT_ROOT = Path(__file__).resolve().parents[1]
if TT_ROOT.as_posix() not in sys.path:
    sys.path.insert(0, TT_ROOT.as_posix())

from scripts.import_harvest_time_report import _configure_database_url, _make_async_url, _resolve_database_url

SOURCE_NAME = "drafting documents"
TARGET_NAME = "drafting"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


def _configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


@dataclass
class ProjectPlan:
    project_id: str
    source_tasks: list[Any]
    target_task: Any | None


async def _run(*, database_url: str, execute: bool) -> int:
    from infrastructure.models import TimeEntryModel, TimeManagerClientTaskModel

    engine = create_async_engine(_make_async_url(database_url), echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            rows = list((await session.execute(select(TimeManagerClientTaskModel))).scalars().all())
            by_project: dict[str, list[TimeManagerClientTaskModel]] = defaultdict(list)
            for row in rows:
                by_project[row.project_id].append(row)

            plans: list[ProjectPlan] = []
            for project_id, tasks in by_project.items():
                source_tasks = [t for t in tasks if _norm(t.name) == SOURCE_NAME]
                if not source_tasks:
                    continue
                target_tasks = [t for t in tasks if _norm(t.name) == TARGET_NAME]
                target_task = sorted(
                    target_tasks,
                    key=lambda t: (t.created_at or _now_utc(), t.id),
                )[0] if target_tasks else None
                plans.append(ProjectPlan(project_id=project_id, source_tasks=source_tasks, target_task=target_task))

            if not plans:
                print("Ничего делать не нужно: задач 'Drafting Documents' не найдено.")
                return 0

            print(f"Проектов с 'Drafting Documents': {len(plans)}")

            total_source_tasks = 0
            total_relinked_entries = 0
            created_targets = 0

            for plan in plans:
                source_ids = [t.id for t in plan.source_tasks]
                total_source_tasks += len(source_ids)
                relink_count = int(
                    (
                        await session.execute(
                            select(func.count(TimeEntryModel.id)).where(
                                TimeEntryModel.task_id.in_(source_ids)
                            )
                        )
                    ).scalar_one()
                    or 0
                )
                total_relinked_entries += relink_count

                if not execute:
                    target_note = f"target={plan.target_task.id}" if plan.target_task else "target=<create>"
                    print(
                        f"[DRY] project={plan.project_id}: source_tasks={len(source_ids)}, "
                        f"entries_to_move={relink_count}, {target_note}"
                    )
                    continue

                target_task = plan.target_task
                if target_task is None:
                    sample = plan.source_tasks[0]
                    target_task = TimeManagerClientTaskModel(
                        id=str(uuid.uuid4()),
                        project_id=plan.project_id,
                        name="Drafting",
                        default_billable_rate=sample.default_billable_rate,
                        billable_by_default=any(t.billable_by_default for t in plan.source_tasks),
                        created_at=_now_utc(),
                        updated_at=None,
                    )
                    session.add(target_task)
                    await session.flush()
                    created_targets += 1

                if source_ids:
                    await session.execute(
                        update(TimeEntryModel)
                        .where(TimeEntryModel.task_id.in_(source_ids))
                        .values(task_id=target_task.id)
                    )
                    await session.execute(
                        delete(TimeManagerClientTaskModel).where(
                            TimeManagerClientTaskModel.id.in_(source_ids)
                        )
                    )

                print(
                    f"[OK] project={plan.project_id}: moved_entries={relink_count}, "
                    f"deleted_tasks={len(source_ids)}, target={target_task.id}"
                )

            if execute:
                await session.commit()
                print(
                    f"\nГотово: удалено задач 'Drafting Documents'={total_source_tasks}, "
                    f"перенесено time entries={total_relinked_entries}, "
                    f"создано задач 'Drafting'={created_targets}."
                )
            else:
                print(
                    f"\nDRY-RUN: найдено задач 'Drafting Documents'={total_source_tasks}, "
                    f"time entries для переноса={total_relinked_entries}."
                )
                print("Для применения запустите с --execute.")

    finally:
        await engine.dispose()
    return 0


def main() -> int:
    _configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description="Удалить 'Drafting Documents' и заменить на 'Drafting' во всех проектах."
    )
    parser.add_argument("--database-url", type=str, default="", help="PostgreSQL URL")
    parser.add_argument("--execute", action="store_true", help="Применить изменения")
    args = parser.parse_args()

    database_url = _resolve_database_url(args.database_url or None)
    _configure_database_url(database_url)
    return asyncio.run(_run(database_url=database_url, execute=args.execute))


if __name__ == "__main__":
    raise SystemExit(main())
