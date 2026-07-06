#!/usr/bin/env python3
"""
Поиск дубликатов записей учёта времени.

Дубликат — две и более записи одного пользователя с совпадением:
  • день (work_date)
  • задача (task_id)
  • заметка (description)
  • время (rounded_hours)
  • сумма оплаты (billable amount)

=== Запуск на сервере (python, без docker) ===

  cd /path/to/tickets-back
  python time_tracking/scripts/find_duplicate_time_entries.py -o duplicates.csv

URL БД берётся из .env (DATABASE_URL или TIME_TRACKING_DATABASE_URL)
или из переменных окружения. Свой файл:

  python time_tracking/scripts/find_duplicate_time_entries.py --env-file /path/to/.env -o dup.csv

Явный URL:

  python time_tracking/scripts/find_duplicate_time_entries.py \\
    --database-url "postgresql://USER:PASS@127.0.0.1:5432/kosta_time_tracking" \\
    -o duplicates.csv

Фильтры:

  --date-from 2024-01-01 --date-to 2026-06-30
  --auth-user-id 12,45
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

TT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TT_ROOT.parent

_Q6 = Decimal("0.000001")
_Q2 = Decimal("0.01")


def _make_async_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+asyncpg://", 1)
    return u


def _load_dotenv_files(explicit: str | None = None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"Файл .env не найден: {path}")
        load_dotenv(path, override=False)
        print(f"Env: {path}")
        return
    for path in (
        REPO_ROOT / ".env",
        TT_ROOT / ".env",
        Path.cwd() / ".env",
    ):
        if path.is_file():
            load_dotenv(path, override=False)
            print(f"Env: {path}")
            return


def _normalize_database_url(url: str) -> str:
    """При запуске на хосте заменить docker-имена хостов на localhost."""
    u = (url or "").strip()
    if not u:
        return u
    for docker_host in ("@time_tracking_db:", "@users_db:", "@postgres:"):
        if docker_host in u:
            u = u.replace(docker_host, "@127.0.0.1:")
    return u


def _resolve_database_url(cli_url: str | None) -> str:
    if cli_url and cli_url.strip():
        return _normalize_database_url(cli_url.strip())
    for key in ("TIME_TRACKING_DATABASE_URL", "DATABASE_URL"):
        val = (os.environ.get(key) or "").strip()
        if val:
            normalized = _normalize_database_url(val)
            print(f"Подключение: env {key}")
            if normalized != val:
                print("  (хост docker заменён на 127.0.0.1 — запуск вне контейнера)")
            return normalized
    raise SystemExit(
        "Задайте URL PostgreSQL time tracking:\n"
        "  1) положите DATABASE_URL или TIME_TRACKING_DATABASE_URL в .env рядом с проектом\n"
        "  2) export TIME_TRACKING_DATABASE_URL='postgresql://user:pass@127.0.0.1:5432/kosta_time_tracking'\n"
        "  3) --database-url postgresql://...\n"
        "  4) --env-file /path/to/.env"
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


def _norm_note(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal(0)


def _hours_key(v: Decimal) -> str:
    return str(v.quantize(_Q6, rounding=ROUND_HALF_UP))


def _money_key(v: Decimal) -> str:
    return str(v.quantize(_Q2, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class DuplicateKey:
    auth_user_id: int
    work_date: date
    task_id: str
    note_norm: str
    hours_key: str
    amount_key: str
    currency: str


@dataclass
class EntryRow:
    key: DuplicateKey
    entry_id: str
    auth_user_id: int
    user_name: str
    user_email: str
    work_date: date
    task_id: str | None
    task_name: str
    project_id: str | None
    project_name: str
    client_name: str
    description: str
    hours: Decimal
    rounded_hours: Decimal
    is_billable: bool
    billable_amount: Decimal
    currency: str
    created_at: datetime
    voided_at: datetime | None


CSV_COLUMNS = [
    "duplicate_group_id",
    "entries_in_group",
    "entry_id",
    "auth_user_id",
    "user_name",
    "user_email",
    "work_date",
    "task_id",
    "task_name",
    "project_id",
    "project_name",
    "client_name",
    "description",
    "hours",
    "rounded_hours",
    "is_billable",
    "billable_amount",
    "currency",
    "created_at",
    "voided_at",
]


async def _run(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from application.entry_pricing import _billable_amount_for_entry
    from application.report_builder import _load_user_rates
    from infrastructure.models import (
        TimeEntryModel,
        TimeManagerClientModel,
        TimeManagerClientProjectModel,
        TimeManagerClientTaskModel,
        TimeTrackingUserModel,
    )

    engine = create_async_engine(_make_async_url(args.database_url), echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    date_from = date.fromisoformat(args.date_from) if args.date_from else None
    date_to = date.fromisoformat(args.date_to) if args.date_to else None
    auth_filter: set[int] | None = None
    if args.auth_user_id:
        auth_filter = {int(x.strip()) for x in args.auth_user_id.split(",") if x.strip()}

    async with session_factory() as session:
        users = {
            u.auth_user_id: u
            for u in (await session.execute(select(TimeTrackingUserModel))).scalars().all()
        }
        projects = {
            p.id: p
            for p in (await session.execute(select(TimeManagerClientProjectModel))).scalars().all()
        }
        clients = {
            c.id: c
            for c in (await session.execute(select(TimeManagerClientModel))).scalars().all()
        }
        tasks = {
            t.id: t
            for t in (await session.execute(select(TimeManagerClientTaskModel))).scalars().all()
        }

        q = select(TimeEntryModel).order_by(
            TimeEntryModel.auth_user_id,
            TimeEntryModel.work_date,
            TimeEntryModel.created_at,
        )
        if not args.include_voided:
            q = q.where(TimeEntryModel.voided_at.is_(None))
        if date_from is not None:
            q = q.where(TimeEntryModel.work_date >= date_from)
        if date_to is not None:
            q = q.where(TimeEntryModel.work_date <= date_to)
        if auth_filter:
            q = q.where(TimeEntryModel.auth_user_id.in_(sorted(auth_filter)))

        entries = list((await session.execute(q)).scalars().all())
        user_ids = sorted({e.auth_user_id for e in entries})
        rates_map = await _load_user_rates(session, user_ids)

    await engine.dispose()

    parsed: list[EntryRow] = []
    for e in entries:
        u = users.get(e.auth_user_id)
        p = projects.get(e.project_id) if e.project_id else None
        c = clients.get(p.client_id) if p and p.client_id else None
        t = tasks.get(e.task_id) if e.task_id else None
        project_currency = (getattr(p, "currency", None) or "USD") if p else "USD"
        hrs = _d(e.rounded_hours if e.rounded_hours is not None else e.hours)
        amt, cur = _billable_amount_for_entry(
            hrs,
            e.is_billable,
            e.work_date,
            rates_map.get(e.auth_user_id),
            project_currency=project_currency,
            time_entry_project_id=e.project_id,
        )
        key = DuplicateKey(
            auth_user_id=int(e.auth_user_id),
            work_date=e.work_date,
            task_id=(e.task_id or "").strip(),
            note_norm=_norm_note(e.description),
            hours_key=_hours_key(hrs),
            amount_key=_money_key(_d(amt)),
            currency=(cur or project_currency or "USD").strip()[:10],
        )
        parsed.append(
            EntryRow(
                key=key,
                entry_id=e.id,
                auth_user_id=int(e.auth_user_id),
                user_name=(u.display_name or u.email or str(e.auth_user_id)) if u else str(e.auth_user_id),
                user_email=u.email if u else "",
                work_date=e.work_date,
                task_id=e.task_id,
                task_name=t.name if t else "",
                project_id=e.project_id,
                project_name=p.name if p else "",
                client_name=c.name if c else "",
                description=(e.description or "").strip(),
                hours=_d(e.hours),
                rounded_hours=hrs,
                is_billable=bool(e.is_billable),
                billable_amount=_d(amt),
                currency=key.currency,
                created_at=e.created_at,
                voided_at=e.voided_at,
            )
        )

    groups: dict[DuplicateKey, list[EntryRow]] = defaultdict(list)
    for row in parsed:
        groups[row.key].append(row)

    duplicate_groups = {
        k: rows for k, rows in groups.items() if len(rows) >= args.min_group_size
    }
    duplicate_groups_sorted = sorted(
        duplicate_groups.items(),
        key=lambda item: (
            -len(item[1]),
            item[0].auth_user_id,
            item[0].work_date.isoformat(),
            item[0].task_id,
            item[0].note_norm,
        ),
    )

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_dup_entries = sum(len(rows) for rows in duplicate_groups.values())
    group_no = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for key, rows in duplicate_groups_sorted:
            group_no += 1
            group_id = f"DUP-{group_no:05d}"
            rows_sorted = sorted(rows, key=lambda r: (r.created_at, r.entry_id))
            for row in rows_sorted:
                writer.writerow(
                    {
                        "duplicate_group_id": group_id,
                        "entries_in_group": len(rows_sorted),
                        "entry_id": row.entry_id,
                        "auth_user_id": row.auth_user_id,
                        "user_name": row.user_name,
                        "user_email": row.user_email,
                        "work_date": row.work_date.isoformat(),
                        "task_id": row.task_id or "",
                        "task_name": row.task_name,
                        "project_id": row.project_id or "",
                        "project_name": row.project_name,
                        "client_name": row.client_name,
                        "description": row.description,
                        "hours": str(row.hours),
                        "rounded_hours": str(row.rounded_hours),
                        "is_billable": row.is_billable,
                        "billable_amount": str(row.billable_amount),
                        "currency": row.currency,
                        "created_at": row.created_at.isoformat() if row.created_at else "",
                        "voided_at": row.voided_at.isoformat() if row.voided_at else "",
                    }
                )

    users_affected = len({k.auth_user_id for k in duplicate_groups})
    print()
    print("=== Дубликаты записей Time Tracking ===")
    print(f"Проверено записей:     {len(parsed):,}")
    print(f"Групп дубликатов:      {len(duplicate_groups):,}")
    print(f"Записей в дубликатах:  {total_dup_entries:,}")
    print(f"Пользователей:         {users_affected:,}")
    print(f"Файл:                  {out_path}")
    if duplicate_groups_sorted:
        top = duplicate_groups_sorted[0]
        sample = top[1][0]
        print()
        print(
            f"Пример (группа из {len(top[1])}): "
            f"{sample.user_name}, {sample.work_date}, "
            f"задача «{sample.task_name or '—'}», {sample.rounded_hours} ч, "
            f"{sample.billable_amount} {sample.currency}"
        )
    else:
        print("\nДубликаты не найдены по заданным критериям.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    default_out = (
        REPO_ROOT
        / "time_tracking"
        / "scripts"
        / f"duplicate_time_entries_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    )
    p = argparse.ArgumentParser(
        description="Найти дубликаты записей time tracking и сохранить в CSV (UTF-8 BOM для Excel).",
    )
    p.add_argument(
        "--env-file",
        default=None,
        help="Путь к .env (иначе ищется .env в корне проекта / time_tracking / cwd).",
    )
    p.add_argument(
        "--database-url",
        dest="database_url",
        default=None,
        help="PostgreSQL URL (иначе TIME_TRACKING_DATABASE_URL / DATABASE_URL).",
    )
    p.add_argument(
        "-o",
        "--output",
        default=str(default_out),
        help=f"Путь к CSV (по умолчанию: {default_out.name}).",
    )
    p.add_argument(
        "--date-from",
        default=None,
        help="Начало периода YYYY-MM-DD (необязательно).",
    )
    p.add_argument(
        "--date-to",
        default=None,
        help="Конец периода YYYY-MM-DD (необязательно).",
    )
    p.add_argument(
        "--auth-user-id",
        default=None,
        help="Только указанные auth user id, через запятую.",
    )
    p.add_argument(
        "--include-voided",
        action="store_true",
        help="Включать void-записи (по умолчанию только активные).",
    )
    p.add_argument(
        "--min-group-size",
        type=int,
        default=2,
        help="Минимум записей в группе, чтобы считать дубликатом (по умолчанию 2).",
    )
    return p


def main() -> None:
    _configure_stdio_utf8()
    args = _build_parser().parse_args()
    _load_dotenv_files(args.env_file)
    database_url = _resolve_database_url(args.database_url)
    _configure_database_url(database_url)
    args.database_url = database_url
    if args.min_group_size < 2:
        raise SystemExit("--min-group-size должен быть >= 2")
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
