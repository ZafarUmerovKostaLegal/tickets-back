"""Сверка Harvest CSV с time tracking (только чтение, без изменений в БД).

Примеры:

  python scripts/verify_harvest_time_report.py --file harvest_time_report_from2018-01-08to2026-06-08.csv
  python scripts/verify_harvest_time_report.py --file report.csv --only-checkpoint
  python scripts/verify_harvest_time_report.py --file report.csv --client "EVYAP INTERNATIONAL" --project "Company Establishment"

URL БД: TIME_TRACKING_DATABASE_URL или --database-url.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

TT_ROOT = Path(__file__).resolve().parents[1]
if TT_ROOT.as_posix() not in sys.path:
    sys.path.insert(0, TT_ROOT.as_posix())

from scripts.import_harvest_time_report import (  # noqa: E402
    HarvestRow,
    _build_harvest_meta_maps,
    _build_harvest_project_archived_map,
    _billable_default_for_harvest_task,
    _configure_database_url,
    _load_projects_catalog,
    _expected_task_hours_map,
    _load_checkpoint,
    _load_rows,
    _make_async_url,
    _merge_project_pairs,
    _norm,
    _project_key,
    _quantize_harvest_hours,
    _resolve_database_url,
    _resolve_harvest_file,
    _default_checkpoint_path,
)


def _rows_for_project(
    rows: list[HarvestRow], client_name: str, project_name: str
) -> list[HarvestRow]:
    ckey = _norm(client_name)
    pkey = _norm(project_name)
    return [r for r in rows if _norm(r.client_name) == ckey and _norm(r.project_name) == pkey]


def _expected_hours_breakdown(
    rows: list[HarvestRow], client_name: str, project_name: str
) -> tuple[Decimal, Decimal, Decimal]:
    total = Decimal("0")
    billable = Decimal("0")
    proj_rows = _rows_for_project(rows, client_name, project_name)
    for r in proj_rows:
        total += r.hours
        if r.is_billable:
            billable += r.hours
    return _quantize_harvest_hours(total), _quantize_harvest_hours(billable), _quantize_harvest_hours(
        total - billable
    )


def _configure_stdio_utf8() -> None:
    """Avoid argparse/help crashes on Windows non-UTF consoles."""
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


async def _run_verify(
    *,
    path: Path,
    database_url: str,
    harvest_source_name: str | None,
    only_checkpoint: bool,
    projects_file: Path | None,
    client_filter: str | None,
    project_filter: str | None,
    show_ok: bool,
    limit_mismatches: int,
) -> int:
    from sqlalchemy import and_, case, func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from infrastructure.models import (
        TimeEntryModel,
        TimeManagerClientModel,
        TimeManagerClientProjectModel,
        TimeManagerClientTaskModel,
    )

    rows = _load_rows(path)
    catalog = _load_projects_catalog(projects_file) if projects_file else []
    if not rows and not catalog:
        print("Файл пуст.")
        return 1

    source_name = harvest_source_name or path.name
    harvest_prefix = f"harvest-import:{source_name}:"
    all_pairs = _merge_project_pairs(rows, catalog)
    client_currency_map, project_currency_map, _ = _build_harvest_meta_maps(rows, catalog)
    project_archived_map = _build_harvest_project_archived_map(catalog)

    if only_checkpoint:
        ckpt_path = _default_checkpoint_path(path)
        if not ckpt_path.is_file():
            print(f"Checkpoint не найден: {ckpt_path}")
            return 1
        ckpt = _load_checkpoint(ckpt_path, path, reset=False)
        done_keys = set(ckpt.get("completed_project_keys") or [])
        all_pairs = [p for p in all_pairs if _project_key(*p) in done_keys]
        print(f"Проверка только проектов из checkpoint ({len(all_pairs)} шт.)")

    if client_filter:
        cf = _norm(client_filter)
        all_pairs = [p for p in all_pairs if _norm(p[0]) == cf]
    if project_filter:
        pf = _norm(project_filter)
        all_pairs = [p for p in all_pairs if _norm(p[1]) == pf]

    print(f"Файл: {path}")
    print(f"Harvest prefix: {harvest_prefix}")
    print(f"Строк в CSV: {len(rows)}")
    if catalog:
        print(f"Проектов в каталоге: {len(catalog)}")
    print(f"Проектов к проверке: {len(all_pairs)}")
    print()

    engine = create_async_engine(_make_async_url(database_url), echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    ok_projects = 0
    mismatch_projects = 0
    missing_projects = 0
    mismatch_lines = 0
    no_limit = limit_mismatches <= 0

    def _can_print_mismatch() -> bool:
        return no_limit or mismatch_lines < limit_mismatches

    async def find_client(session: AsyncSession, name: str) -> TimeManagerClientModel | None:
        target = _norm(name)
        r = await session.execute(select(TimeManagerClientModel))
        for c in r.scalars().all():
            if _norm(c.name) == target:
                return c
        return None

    async def find_project(
        session: AsyncSession, client_id: str, name: str
    ) -> TimeManagerClientProjectModel | None:
        target = _norm(name)
        r = await session.execute(
            select(TimeManagerClientProjectModel).where(
                TimeManagerClientProjectModel.client_id == client_id
            )
        )
        for p in r.scalars().all():
            if _norm(p.name) == target:
                return p
        return None

    async def harvest_entries_for_project(session: AsyncSession, project_id: str):
        r = await session.execute(
            select(TimeEntryModel).where(
                TimeEntryModel.project_id == project_id,
                TimeEntryModel.voided_at.is_(None),
                TimeEntryModel.external_reference_url.like(f"{harvest_prefix}%"),
            )
        )
        return list(r.scalars().all())

    async def aggregate_harvest_hours(session: AsyncSession, project_id: str):
        q = (
            select(
                func.coalesce(func.sum(TimeEntryModel.hours), 0).label("total"),
                func.coalesce(
                    func.sum(
                        case((TimeEntryModel.is_billable.is_(True), TimeEntryModel.hours), else_=0)
                    ),
                    0,
                ).label("billable"),
                func.coalesce(
                    func.sum(
                        case((TimeEntryModel.is_billable.is_(False), TimeEntryModel.hours), else_=0)
                    ),
                    0,
                ).label("non_billable"),
                func.count(TimeEntryModel.id).label("rows"),
            )
            .where(
                TimeEntryModel.project_id == project_id,
                TimeEntryModel.voided_at.is_(None),
                TimeEntryModel.external_reference_url.like(f"{harvest_prefix}%"),
            )
        )
        row = (await session.execute(q)).one()
        return (
            _quantize_harvest_hours(Decimal(str(row.total))),
            _quantize_harvest_hours(Decimal(str(row.billable))),
            _quantize_harvest_hours(Decimal(str(row.non_billable))),
            int(row.rows or 0),
        )

    async def task_breakdown(session: AsyncSession, project_id: str):
        q = (
            select(
                TimeManagerClientTaskModel.name,
                func.coalesce(func.sum(TimeEntryModel.hours), 0).label("total"),
                func.coalesce(
                    func.sum(
                        case((TimeEntryModel.is_billable.is_(True), TimeEntryModel.hours), else_=0)
                    ),
                    0,
                ).label("billable"),
                func.coalesce(
                    func.sum(
                        case((TimeEntryModel.is_billable.is_(False), TimeEntryModel.hours), else_=0)
                    ),
                    0,
                ).label("non_billable"),
                TimeManagerClientTaskModel.billable_by_default,
            )
            .select_from(TimeManagerClientTaskModel)
            .outerjoin(
                TimeEntryModel,
                and_(
                    TimeEntryModel.task_id == TimeManagerClientTaskModel.id,
                    TimeEntryModel.voided_at.is_(None),
                    TimeEntryModel.external_reference_url.like(f"{harvest_prefix}%"),
                ),
            )
            .where(TimeManagerClientTaskModel.project_id == project_id)
            .group_by(
                TimeManagerClientTaskModel.id,
                TimeManagerClientTaskModel.name,
                TimeManagerClientTaskModel.billable_by_default,
            )
        )
        r = await session.execute(q)
        out: dict[str, tuple[str, Decimal, Decimal, Decimal, bool]] = {}
        for row in r.all():
            key = _norm(str(row.name))
            out[key] = (
                str(row.name),
                _quantize_harvest_hours(Decimal(str(row.total))),
                _quantize_harvest_hours(Decimal(str(row.billable))),
                _quantize_harvest_hours(Decimal(str(row.non_billable))),
                bool(row.billable_by_default),
            )
        return out

    try:
        async with session_factory() as session:
            for client_name, project_name in all_pairs:
                proj_rows = _rows_for_project(rows, client_name, project_name)
                exp_total, exp_bill, exp_non = _expected_hours_breakdown(
                    rows, client_name, project_name
                )
                exp_row_count = len(proj_rows)
                expected_refs = {r.import_ref(source_name) for r in proj_rows}

                client = await find_client(session, client_name)
                if client is None:
                    missing_projects += 1
                    if _can_print_mismatch():
                        print(f"[НЕТ В БД] клиент «{client_name}» / «{project_name}» "
                              f"(файл {exp_row_count} строк, {exp_total} ч)")
                        mismatch_lines += 1
                    continue

                project = await find_project(session, client.id, project_name)
                if project is None:
                    missing_projects += 1
                    if _can_print_mismatch():
                        print(f"[НЕТ В БД] {client_name} / «{project_name}» "
                              f"(файл {exp_row_count} строк, {exp_total} ч)")
                        mismatch_lines += 1
                    continue

                db_total, db_bill, db_non, db_rows = await aggregate_harvest_hours(
                    session, project.id
                )
                entries = await harvest_entries_for_project(session, project.id)
                db_refs = {
                    (e.external_reference_url or "").strip()
                    for e in entries
                    if (e.external_reference_url or "").strip()
                }

                project_ok = True
                issues: list[str] = []
                pair_key = _project_key(client_name, project_name)
                exp_client_cur = client_currency_map.get(_norm(client_name), "USD")
                exp_project_cur = project_currency_map.get(pair_key, exp_client_cur)
                exp_archived = project_archived_map.get(pair_key)

                if (client.currency or "").strip().upper()[:10] != exp_client_cur:
                    project_ok = False
                    issues.append(
                        f"валюта клиента: файл {exp_client_cur}, БД {client.currency}"
                    )
                if (project.currency or "").strip().upper()[:10] != exp_project_cur:
                    project_ok = False
                    issues.append(
                        f"валюта проекта: файл {exp_project_cur}, БД {project.currency}"
                    )
                if exp_archived is not None and bool(project.is_archived) != exp_archived:
                    project_ok = False
                    issues.append(
                        f"архивность проекта: файл {exp_archived}, БД {bool(project.is_archived)}"
                    )

                if db_total != exp_total:
                    project_ok = False
                    issues.append(f"часы: файл {exp_total}, БД {db_total}")
                if db_bill != exp_bill:
                    project_ok = False
                    issues.append(f"billable: файл {exp_bill}, БД {db_bill}")
                if db_non != exp_non:
                    project_ok = False
                    issues.append(f"non-billable: файл {exp_non}, БД {db_non}")
                if db_rows != exp_row_count:
                    project_ok = False
                    issues.append(f"строк: файл {exp_row_count}, БД {db_rows}")

                missing_refs = sorted(expected_refs - db_refs)
                extra_refs = sorted(db_refs - expected_refs)
                if missing_refs:
                    project_ok = False
                    issues.append(f"нет в БД: {len(missing_refs)} ref "
                                  f"(напр. {missing_refs[:2]})")
                if extra_refs:
                    project_ok = False
                    issues.append(f"лишние в БД: {len(extra_refs)} ref "
                                  f"(напр. {extra_refs[:2]})")

                expected_tasks = _expected_task_hours_map(proj_rows)
                db_tasks = await task_breakdown(session, project.id)
                for key, (exp_t, exp_b, exp_n) in expected_tasks.items():
                    exp_t = _quantize_harvest_hours(exp_t)
                    exp_b = _quantize_harvest_hours(exp_b)
                    exp_n = _quantize_harvest_hours(exp_n)
                    if key not in db_tasks:
                        if exp_t > 0:
                            project_ok = False
                            issues.append(f"задача «{key}»: нет в БД ({exp_t} ч в файле)")
                        continue
                    _n, db_t, db_b, db_n, db_flag = db_tasks[key]
                    exp_flag = _billable_default_for_harvest_task(key, proj_rows)
                    if db_t != exp_t or db_b != exp_b or db_n != exp_n or db_flag != exp_flag:
                        project_ok = False
                        issues.append(
                            f"задача «{_n}»: файл {exp_t} ч, БД {db_t} ч"
                        )

                if project_ok:
                    ok_projects += 1
                    if show_ok:
                        print(
                            f"[OK] {client_name} / {project_name}: "
                            f"{exp_row_count} строк, {exp_total} ч"
                        )
                else:
                    mismatch_projects += 1
                    if _can_print_mismatch():
                        print(f"[РАСХОЖДЕНИЕ] {client_name} / {project_name}")
                        for issue in issues:
                            print(f"    - {issue}")
                        mismatch_lines += 1

            if not no_limit and mismatch_lines >= limit_mismatches and (
                mismatch_projects + missing_projects > mismatch_lines
            ):
                print(f"\n... показаны первые {limit_mismatches} проблем (используйте --limit)")

    finally:
        await engine.dispose()

    total = len(all_pairs)
    print()
    print("=== Итог ===")
    print(f"Проектов проверено: {total}")
    print(f"  OK:              {ok_projects}")
    print(f"  Расхождения:     {mismatch_projects}")
    print(f"  Нет в БД:        {missing_projects}")
    if total:
        pct = 100.0 * ok_projects / total
        print(f"Совпадение:        {ok_projects}/{total} ({pct:.1f}%)")

    csv_hours = _quantize_harvest_hours(sum((r.hours for r in rows), Decimal("0")))
    print(f"Часы в CSV (все проекты файла): {csv_hours}")

    if mismatch_projects or missing_projects:
        print("\nИмпорт не полный или данные разошлись. Продолжите --execute --batch-size 1 "
              "или проверьте проблемные проекты с --client / --project.")
        return 1

    print("\nCSV и БД совпадают 1:1 по всем проверенным проектам.")
    return 0


def main() -> int:
    _configure_stdio_utf8()
    p = argparse.ArgumentParser(
        description="Сверка Harvest CSV с time tracking (read-only)."
    )
    p.add_argument("--file", type=Path, required=True, help="Путь к .csv / .xlsx")
    p.add_argument("--database-url", type=str, default="", help="PostgreSQL URL")
    p.add_argument(
        "--harvest-source-name",
        type=str,
        default="",
        help="Имя файла в harvest-import: (по умолчанию — basename --file).",
    )
    p.add_argument(
        "--only-checkpoint",
        action="store_true",
        help="Проверить только проекты, отмеченные в .harvest-import.checkpoint.json",
    )
    p.add_argument(
        "--projects-file",
        type=Path,
        default=None,
        help="Опциональный каталог всех проектов Harvest (.csv/.xlsx) для проверки валют/статусов 1:1.",
    )
    p.add_argument("--client", type=str, default="", help="Фильтр по клиенту")
    p.add_argument("--project", type=str, default="", help="Фильтр по проекту")
    p.add_argument(
        "--show-ok",
        action="store_true",
        help="Печатать также совпавшие проекты",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="Максимум строк с проблемами в выводе (0 = без лимита)",
    )
    args = p.parse_args()

    harvest_file = _resolve_harvest_file(args.file)
    if harvest_file is None:
        print(f"Файл не найден: {args.file}")
        return 1
    projects_file = None
    if args.projects_file:
        projects_file = _resolve_harvest_file(args.projects_file)
        if projects_file is None:
            print(f"Файл каталога проектов не найден: {args.projects_file}")
            return 1

    database_url = _resolve_database_url(args.database_url or None)
    _configure_database_url(database_url)

    try:
        return asyncio.run(
            _run_verify(
                path=harvest_file,
                database_url=database_url,
                harvest_source_name=(args.harvest_source_name or "").strip() or None,
                only_checkpoint=args.only_checkpoint,
                projects_file=projects_file,
                client_filter=(args.client or "").strip() or None,
                project_filter=(args.project or "").strip() or None,
                show_ok=args.show_ok,
                limit_mismatches=max(0, args.limit),
            )
        )
    except KeyboardInterrupt:
        print("\nОстановлено (Ctrl+C).")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
