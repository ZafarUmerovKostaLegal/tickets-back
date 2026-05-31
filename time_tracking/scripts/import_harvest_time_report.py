"""Импорт клиентов, проектов и записей времени из отчёта Harvest (.xlsx).

Запуск на сервере **без Docker**:

  cd /path/to/tickets-back
  export TIME_TRACKING_DATABASE_URL="postgresql://user:pass@host:5432/kosta_time_tracking"
  pip install -r time_tracking/requirements.txt

  python time_tracking/scripts/import_harvest_time_report.py --dry-run
  python time_tracking/scripts/import_harvest_time_report.py --execute

По умолчанию ищется: harvest_time_report_from2023-01-23to2026-05-26.xlsx
(time_tracking/, timetrackinck/, /tmp/ в контейнере).

URL БД: --database-url или env TIME_TRACKING_DATABASE_URL / DATABASE_URL.

Пользователи сопоставляются с time_tracking_users по ФИО (First Name + Last Name ↔ display_name).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

TT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TT_ROOT.parent
HARVEST_XLSX_NAME = "harvest_time_report_from2023-01-23to2026-05-26.xlsx"
DEFAULT_XLSX = TT_ROOT / HARVEST_XLSX_NAME


def _harvest_file_candidates(preferred: Path) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []

    def add(p: Path) -> None:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)

    add(preferred)
    add(DEFAULT_XLSX)
    add(Path("/tmp") / HARVEST_XLSX_NAME)
    add(REPO_ROOT / "timetrackinck" / HARVEST_XLSX_NAME)
    add(Path.cwd() / HARVEST_XLSX_NAME)
    add(Path.cwd() / "timetrackinck" / HARVEST_XLSX_NAME)
    return out


def _resolve_harvest_file(preferred: Path) -> Path | None:
    for candidate in _harvest_file_candidates(preferred):
        if candidate.is_file():
            resolved = candidate.resolve()
            if str(resolved) != str(preferred):
                print(f"Используется файл: {resolved}")
            return resolved
    return None


def _make_async_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+asyncpg://", 1)
    return u


def _resolve_database_url(cli_url: str | None) -> str:
    if cli_url and cli_url.strip():
        return cli_url.strip()
    for key in ("TIME_TRACKING_DATABASE_URL", "DATABASE_URL"):
        val = (os.environ.get(key) or "").strip()
        if val:
            print(f"Подключение: env {key}")
            return val
    raise SystemExit(
        "Задайте URL PostgreSQL time tracking:\n"
        "  export TIME_TRACKING_DATABASE_URL='postgresql://user:pass@host:5432/kosta_time_tracking'\n"
        "или: --database-url postgresql://..."
    )


def _configure_database_url(database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    os.environ["TIME_TRACKING_DATABASE_URL"] = database_url
    if TT_ROOT.as_posix() not in sys.path:
        sys.path.insert(0, TT_ROOT.as_posix())
    try:
        from infrastructure.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def _norm(s: str | None) -> str:
    return " ".join((s or "").strip().lower().split())


def _parse_currency(raw: object) -> str:
    text = str(raw or "").strip().upper()
    for code in ("EUR", "USD", "UZS", "GBP", "RUB"):
        if code in text:
            return code
    return "EUR"


def _parse_billable(raw: object) -> bool:
    return str(raw or "").strip().lower() in ("yes", "true", "1", "y")


def _parse_work_date(raw: object) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_hours(raw: object) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        h = Decimal(str(raw))
    except Exception:
        return None
    if h <= 0:
        return None
    return h


@dataclass(frozen=True)
class HarvestRow:
    work_date: date
    client_name: str
    project_name: str
    project_code: str | None
    task_name: str
    notes: str | None
    hours: Decimal
    is_billable: bool
    first_name: str
    last_name: str
    employee_id: str | None
    currency: str
    external_reference_url: str | None

    @property
    def harvest_user_key(self) -> str:
        return _norm(f"{self.first_name} {self.last_name}")


def _load_rows(path: Path) -> list[HarvestRow]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["Harvest"] if "Harvest" in wb.sheetnames else wb[wb.sheetnames[0]]
        out: list[HarvestRow] = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or all(c is None or str(c).strip() == "" for c in row):
                continue
            work_date = _parse_work_date(row[0])
            client_name = str(row[1] or "").strip()
            project_name = str(row[2] or "").strip()
            if not work_date or not client_name or not project_name:
                continue
            hours = _parse_hours(row[6])
            if hours is None:
                continue
            first_name = str(row[10] or "").strip()
            last_name = str(row[11] or "").strip()
            if not first_name and not last_name:
                continue
            out.append(
                HarvestRow(
                    work_date=work_date,
                    client_name=client_name,
                    project_name=project_name,
                    project_code=(str(row[3]).strip() if row[3] not in (None, "") else None),
                    task_name=str(row[4] or "Other research").strip() or "Other research",
                    notes=(str(row[5]).strip() if row[5] not in (None, "") else None),
                    hours=hours,
                    is_billable=_parse_billable(row[7]),
                    first_name=first_name,
                    last_name=last_name,
                    employee_id=(str(row[12]).strip() if row[12] not in (None, "") else None),
                    currency=_parse_currency(row[19] if len(row) > 19 else None),
                    external_reference_url=(
                        str(row[20]).strip() if len(row) > 20 and row[20] not in (None, "") else None
                    ),
                )
            )
        return out
    finally:
        wb.close()


async def _run(*, path: Path, execute: bool, database_url: str) -> int:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from application.client_expense_category_defaults import seed_default_expense_categories_for_client
    from application.client_task_defaults import seed_default_common_tasks_for_project
    from application.time_rounding import seconds_from_hours
    from infrastructure.models import (
        TimeEntryModel,
        TimeManagerClientModel,
        TimeManagerClientProjectModel,
        TimeManagerClientTaskModel,
        TimeTrackingUserModel,
    )
    from infrastructure.repository_access import UserProjectAccessRepository
    from infrastructure.repository_clients import ClientProjectRepository, ClientRepository, ClientTaskRepository
    from infrastructure.repository_entries import TimeEntryRepository

    rows = _load_rows(path)
    if not rows:
        print("Файл пуст или не содержит строк данных.")
        return 1

    print(f"Файл: {path}")
    print(f"Строк в отчёте: {len(rows)}")
    print(f"Клиентов: {len({r.client_name for r in rows})}")
    print(f"Проектов: {len({(r.client_name, r.project_name) for r in rows})}")
    print(f"Пользователей Harvest: {len({r.harvest_user_key for r in rows})}")

    engine = create_async_engine(_make_async_url(database_url), echo=False)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    def build_user_index(users: list[TimeTrackingUserModel]) -> dict[str, int]:
        index: dict[str, int] = {}
        for u in users:
            auth_id = int(u.auth_user_id)
            if u.display_name:
                index[_norm(u.display_name)] = auth_id
            if u.email:
                local = u.email.split("@", 1)[0].replace(".", " ")
                index[_norm(local)] = auth_id
        return index

    def match_auth_user_id(row: HarvestRow, index: dict[str, int]) -> int | None:
        key = row.harvest_user_key
        if key in index:
            return index[key]
        rev = _norm(f"{row.last_name} {row.first_name}")
        if rev in index:
            return index[rev]
        return None

    async def find_client_by_name(session: AsyncSession, name: str) -> TimeManagerClientModel | None:
        target = _norm(name)
        r = await session.execute(select(TimeManagerClientModel))
        for c in r.scalars().all():
            if _norm(c.name) == target:
                return c
        return None

    async def find_project(
        session: AsyncSession,
        client_id: str,
        name: str,
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

    async def task_map_for_project(session: AsyncSession, project_id: str) -> dict[str, str]:
        r = await session.execute(
            select(TimeManagerClientTaskModel.name, TimeManagerClientTaskModel.id).where(
                TimeManagerClientTaskModel.project_id == project_id
            )
        )
        return {_norm(name): str(tid) for name, tid in r.all()}

    async def entry_exists(
        session: AsyncSession,
        *,
        auth_user_id: int,
        work_date: date,
        project_id: str,
        task_id: str | None,
        hours: Decimal,
        description: str | None,
    ) -> bool:
        q = select(TimeEntryModel.id).where(
            TimeEntryModel.auth_user_id == auth_user_id,
            TimeEntryModel.work_date == work_date,
            TimeEntryModel.project_id == project_id,
            TimeEntryModel.hours == hours,
            TimeEntryModel.voided_at.is_(None),
        )
        if task_id:
            q = q.where(TimeEntryModel.task_id == task_id)
        else:
            q = q.where(TimeEntryModel.task_id.is_(None))
        desc = (description or "").strip()
        if desc:
            q = q.where(TimeEntryModel.description == desc)
        else:
            q = q.where(TimeEntryModel.description.is_(None))
        r = await session.execute(q.limit(1))
        return r.scalar_one_or_none() is not None

    try:
        async with session_factory() as session:
            tt_users = list(
                (
                    await session.execute(
                        select(TimeTrackingUserModel).where(TimeTrackingUserModel.is_archived.is_(False))
                    )
                ).scalars().all()
            )
            if not tt_users:
                print("Нет пользователей в time_tracking_users. Сначала restore_tt_users_from_auth_db.py")
                return 1

            user_index = build_user_index(tt_users)
            unmatched = sorted({r.harvest_user_key for r in rows if match_auth_user_id(r, user_index) is None})
            if unmatched:
                print("\nНе найдены в TT (display_name):")
                for name in unmatched:
                    print(f"  - {name}")
                print("Импорт продолжится только для сопоставленных пользователей.")

            stats = Counter()
            client_repo = ClientRepository(session)
            project_repo = ClientProjectRepository(session)
            task_repo = ClientTaskRepository(session)
            entry_repo = TimeEntryRepository(session)
            access_repo = UserProjectAccessRepository(session)

            client_cache: dict[str, str] = {}
            project_cache: dict[tuple[str, str], str] = {}
            task_cache: dict[str, dict[str, str]] = {}
            client_currency: dict[str, str] = {}
            for hr in rows:
                client_currency.setdefault(_norm(hr.client_name), hr.currency)
            granter: int | None = None

            for hr in rows:
                auth_user_id = match_auth_user_id(hr, user_index)
                if auth_user_id is None:
                    stats["skipped_user"] += 1
                    continue
                if granter is None:
                    granter = auth_user_id

                ckey = _norm(hr.client_name)
                if ckey not in client_cache:
                    existing = await find_client_by_name(session, hr.client_name)
                    if existing:
                        client_cache[ckey] = existing.id
                        stats["client_exists"] += 1
                    elif execute:
                        created = await client_repo.create(
                            name=hr.client_name,
                            address=None,
                            currency=client_currency.get(ckey, "EUR"),
                            invoice_due_mode="custom",
                            invoice_due_days_after_issue=30,
                            tax_percent=None,
                            tax2_percent=None,
                            discount_percent=None,
                        )
                        await seed_default_expense_categories_for_client(session, created.id)
                        client_cache[ckey] = created.id
                        stats["client_created"] += 1
                    else:
                        client_cache[ckey] = f"<new:{hr.client_name}>"
                        stats["client_created"] += 1

                client_id = client_cache[ckey]
                if client_id.startswith("<new:"):
                    stats["entry_planned"] += 1
                    continue

                pkey = (client_id, _norm(hr.project_name))
                if pkey not in project_cache:
                    existing_p = await find_project(session, client_id, hr.project_name)
                    if existing_p:
                        project_cache[pkey] = existing_p.id
                        stats["project_exists"] += 1
                    elif execute:
                        proj_dates = [
                            r.work_date
                            for r in rows
                            if _norm(r.client_name) == ckey and _norm(r.project_name) == _norm(hr.project_name)
                        ]
                        created_p = await project_repo.create(
                            client_id=client_id,
                            name=hr.project_name,
                            code=hr.project_code,
                            start_date=min(proj_dates) if proj_dates else hr.work_date,
                            end_date=max(proj_dates) if proj_dates else None,
                            notes="Imported from Harvest",
                            report_visibility="managers_only",
                            project_type="time_and_materials",
                            currency=client_currency.get(ckey, hr.currency),
                        )
                        await seed_default_common_tasks_for_project(session, created_p.id)
                        project_cache[pkey] = created_p.id
                        stats["project_created"] += 1
                    else:
                        project_cache[pkey] = f"<new:{hr.project_name}>"
                        stats["project_created"] += 1

                project_id = project_cache[pkey]
                if project_id.startswith("<new:"):
                    stats["entry_planned"] += 1
                    continue

                if project_id not in task_cache:
                    await seed_default_common_tasks_for_project(session, project_id)
                    task_cache[project_id] = await task_map_for_project(session, project_id)

                tmap = task_cache[project_id]
                tname = _norm(hr.task_name)
                task_id = tmap.get(tname)
                if not task_id and execute:
                    row_task = await task_repo.create(
                        project_id=project_id,
                        name=hr.task_name,
                        default_billable_rate=Decimal("0"),
                        billable_by_default=hr.is_billable,
                    )
                    task_id = row_task.id
                    tmap[tname] = task_id
                    stats["task_created"] += 1
                elif not task_id:
                    stats["task_created"] += 1

                description = hr.notes
                if execute:
                    await access_repo.grant_access_if_absent(
                        auth_user_id,
                        project_id,
                        granted_by_auth_user_id=granter,
                        projects=project_repo,
                    )
                    if await entry_exists(
                        session,
                        auth_user_id=auth_user_id,
                        work_date=hr.work_date,
                        project_id=project_id,
                        task_id=task_id,
                        hours=hr.hours,
                        description=description,
                    ):
                        stats["entry_duplicate"] += 1
                        continue
                    sec = seconds_from_hours(hr.hours)
                    if sec < 60:
                        sec = 60
                    try:
                        await entry_repo.create(
                            entry_id=str(uuid.uuid4()),
                            auth_user_id=auth_user_id,
                            work_date=hr.work_date,
                            duration_seconds=sec,
                            hours=hr.hours,
                            is_billable=hr.is_billable,
                            project_id=project_id,
                            task_id=task_id,
                            description=description,
                            external_reference_url=hr.external_reference_url,
                        )
                        stats["entry_created"] += 1
                    except ValueError as e:
                        stats["entry_error"] += 1
                        print(f"  ошибка записи {hr.work_date} {hr.harvest_user_key}: {e}")
                else:
                    stats["entry_planned"] += 1

            if execute:
                await session.commit()
                print("\nИмпорт выполнен.")
            else:
                print("\nDry-run (без записи в БД). Добавьте --execute для импорта.")

            print(
                f"Клиентов создано: {stats['client_created']}, уже было: {stats['client_exists']}; "
                f"проектов создано: {stats['project_created']}, уже было: {stats['project_exists']}; "
                f"задач создано: {stats['task_created']}; "
                f"записей времени: {stats['entry_created'] or stats['entry_planned']} "
                f"(пропуск дубликатов: {stats['entry_duplicate']}, без пользователя: {stats['skipped_user']}, "
                f"ошибок: {stats['entry_error']})."
            )
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Импорт Harvest time report (.xlsx) в time tracking (без Docker).")
    p.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_XLSX,
        help=f"Путь к .xlsx (по умолчанию ищется {HARVEST_XLSX_NAME}).",
    )
    p.add_argument(
        "--database-url",
        type=str,
        default="",
        help="PostgreSQL URL (иначе TIME_TRACKING_DATABASE_URL или DATABASE_URL).",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Только статистика, без записи.")
    g.add_argument("--execute", action="store_true", help="Записать в БД.")
    args = p.parse_args()

    xlsx = _resolve_harvest_file(args.file)
    if xlsx is None:
        print(f"Файл не найден: {args.file}")
        print(f"Ожидаемое имя: {HARVEST_XLSX_NAME}")
        print("Проверьте пути:")
        for c in _harvest_file_candidates(args.file):
            print(f"  - {c}")
        print(
            "\nКонтейнер time_tracking — скопируйте с хоста:\n"
            f"  docker cp timetrackinck/{HARVEST_XLSX_NAME} "
            "$(docker compose ps -q time_tracking):/tmp/" + HARVEST_XLSX_NAME
        )
        return 1

    database_url = _resolve_database_url(args.database_url or None)
    _configure_database_url(database_url)
    return asyncio.run(_run(path=xlsx, execute=args.execute, database_url=database_url))


if __name__ == "__main__":
    raise SystemExit(main())
