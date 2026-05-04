"""Демо-данные для уже существующих пользователей TT (без синтетических auth_user_id).

Берёт из таблицы time_tracking_users всех не заблокированных и не архивных пользователей,
создаёт клиентов/проекты (префикс имени по умолчанию «[demo]»), выдаёт каждому из них доступ
ко всем созданным проектам и добавляет записи времени по каждому пользователю.

Требование: пользователи уже синхронизированы в TT (например restore_tt_users_from_auth_db.py).

Запуск из каталога time_tracking:

  python scripts/seed_tt_demo_for_existing_users.py --dry-run
  python scripts/seed_tt_demo_for_existing_users.py --execute

Параметры см. --help.

Удаление демо-клиентов (записи времени на их проектах и счета с этими клиентами):

  python scripts/delete_mock_clients.py --prefix '[demo]' --execute

Отчётные снимки/лишние пользователи этим скриптом не создаются.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MOCK_CURRENCIES: tuple[str, ...] = ("UZS", "USD", "EUR")

from sqlalchemy import func, select

from application.client_expense_category_defaults import seed_default_expense_categories_for_all_clients
from application.client_task_defaults import seed_default_common_tasks_for_project
from infrastructure.database import async_session_factory
from infrastructure.models import (
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeManagerClientTaskModel,
    TimeTrackingUserModel,
)
from infrastructure.repository_access import UserProjectAccessRepository
from infrastructure.repository_clients import ClientProjectRepository
from infrastructure.repository_entries import TimeEntryRepository
from infrastructure.repository_rates import HourlyRateRepository
from infrastructure.repository_shared import _now_utc


def _billable_rate_amount_for_currency(cur: str, rnd: random.Random) -> Decimal:
    u = cur.upper().strip()[:10]
    if u == "UZS":
        return Decimal(str(rnd.randint(1_800_000, 4_500_000)))
    if u == "EUR":
        return Decimal(str(115 + rnd.randint(0, 55)))
    return Decimal(str(130 + rnd.randint(0, 70)))


def _cost_rate_amount_for_currency(billable: Decimal) -> Decimal:
    return (billable * Decimal("0.38")).quantize(Decimal("0.0001"))


def _weekdays_in_range(d0: date, d1: date) -> list[date]:
    out: list[date] = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


async def _task_ids_for_projects(session, project_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {pid: [] for pid in project_ids}
    if not project_ids:
        return out
    q = select(TimeManagerClientTaskModel.project_id, TimeManagerClientTaskModel.id).where(
        TimeManagerClientTaskModel.project_id.in_(project_ids)
    )
    r = await session.execute(q)
    for pid, tid in r.all():
        out.setdefault(str(pid), []).append(str(tid))
    return out


async def _active_tt_auth_ids(session) -> list[int]:
    q = (
        select(TimeTrackingUserModel.auth_user_id)
        .where(
            TimeTrackingUserModel.is_blocked.is_(False),
            TimeTrackingUserModel.is_archived.is_(False),
        )
        .order_by(TimeTrackingUserModel.auth_user_id.asc())
    )
    return [int(x) for x in (await session.execute(q)).scalars().all()]


async def _preflight_demo_clients(session, *, prefix: str) -> int:
    pfx = prefix.strip()
    if not pfx:
        return 0
    qc = await session.execute(
        select(func.count())
        .select_from(TimeManagerClientModel)
        .where(TimeManagerClientModel.name.ilike(f"{pfx}%"))
    )
    return int(qc.scalar_one() or 0)


async def _ensure_hourly_rates(session, rnd: random.Random, auth_ids: list[int]) -> int:
    """Добавляет недостающие ставки billable/cost по валютам (пропуск при конфликте интервалов)."""
    repo = HourlyRateRepository(session)
    added = 0
    for uid in auth_ids:
        for cur in MOCK_CURRENCIES:
            bill = _billable_rate_amount_for_currency(cur, rnd)
            try:
                await repo.create(
                    auth_user_id=uid,
                    rate_kind="billable",
                    amount=bill,
                    currency=cur,
                    valid_from=None,
                    valid_to=None,
                    applies_to_project_id=None,
                )
                added += 1
            except ValueError:
                pass
            try:
                await repo.create(
                    auth_user_id=uid,
                    rate_kind="cost",
                    amount=_cost_rate_amount_for_currency(bill),
                    currency=cur,
                    valid_from=None,
                    valid_to=None,
                    applies_to_project_id=None,
                )
                added += 1
            except ValueError:
                pass
    return added


async def _run(
    *,
    dry_run: bool,
    force: bool,
    lite: bool,
    client_prefix: str,
    clients_count: int,
    projects_min: int,
    projects_max: int,
    months_back: int,
    entry_weekdays_per_user: int | None,
    seed: int,
    ensure_hourly_rates: bool,
) -> int:
    rnd = random.Random(seed)
    prefix = client_prefix.strip() or "[demo]"
    if lite:
        clients_count = min(clients_count, 4)
        projects_min = min(projects_min, 2)
        projects_max = min(max(projects_max, projects_min), 6)
        months_back = min(months_back, 3)

    if projects_max < projects_min:
        projects_max = projects_min

    today = date.today()
    date_from = today.replace(day=1) - timedelta(days=30 * (months_back - 1))
    date_from = date_from.replace(day=1)
    date_to = today
    weekdays = _weekdays_in_range(date_from, date_to)

    if entry_weekdays_per_user is None:
        raw = max(10, min(len(weekdays), len(weekdays) // 2))
        n_days_user = min(len(weekdays), 6 if lite else raw)
    else:
        n_days_user = max(1, min(len(weekdays), entry_weekdays_per_user))

    async with async_session_factory() as session:
        auth_ids = await _active_tt_auth_ids(session)
        if not auth_ids:
            print(
                "В TT нет активных пользователей (не заблокированных и не архивных). "
                "Сначала синхронизируйте пользователей из auth.",
                file=sys.stderr,
            )
            return 1

        existing_demo = await _preflight_demo_clients(session, prefix=prefix)
        if existing_demo and not force:
            print(
                f"Уже есть клиенты с именем ilike {prefix!r}% ({existing_demo}). "
                "Удалите их или передайте --force.",
                file=sys.stderr,
            )
            return 1

        est_projects = clients_count * (projects_min + projects_max) // 2
        print(
            f"План: активных пользователей TT={len(auth_ids)}, новых клиентов={clients_count}, "
            f"проектов≈{est_projects}, будних дней в периоде={len(weekdays)}, "
            f"дней с записями на пользователя≈{n_days_user}, префикс клиентов={prefix!r}, seed={seed}."
        )
        for uid in auth_ids[:30]:
            row = (
                await session.execute(select(TimeTrackingUserModel).where(TimeTrackingUserModel.auth_user_id == uid))
            ).scalar_one_or_none()
            em = row.email if row else ""
            dn = (row.display_name or "") if row else ""
            print(f"  пользователь auth_user_id={uid}  {dn!r}  <{em}>")
        if len(auth_ids) > 30:
            print(f"  … всего пользователей: {len(auth_ids)}")

        if dry_run:
            print("\n[dry-run] Изменений нет.")
            return 0

        now = _now_utc()

        if ensure_hourly_rates:
            n_rates = await _ensure_hourly_rates(session, rnd, auth_ids)
            await session.flush()
            print(f"\nДобавлено новых строк ставок (billable/cost по валютам): {n_rates}")

        client_ids: list[str] = []
        project_ids: list[str] = []

        for ci in range(clients_count):
            cid = str(uuid.uuid4())
            client_currency = MOCK_CURRENCIES[ci % len(MOCK_CURRENCIES)]
            client_ids.append(cid)
            session.add(
                TimeManagerClientModel(
                    id=cid,
                    name=f"{prefix} Client {ci + 1:02d}",
                    address=None,
                    currency=client_currency,
                    invoice_due_mode="custom",
                    invoice_due_days_after_issue=30,
                    tax_percent=None,
                    tax2_percent=None,
                    discount_percent=None,
                    phone=None,
                    email=None,
                    contact_name=None,
                    contact_phone=None,
                    contact_email=None,
                    is_archived=False,
                    created_at=now,
                    updated_at=None,
                )
            )
            np = rnd.randint(projects_min, projects_max)
            for pj in range(np):
                pid = str(uuid.uuid4())
                project_ids.append(pid)
                session.add(
                    TimeManagerClientProjectModel(
                        id=pid,
                        client_id=cid,
                        name=f"Demo {ci + 1:02d}-{pj + 1:02d}",
                        code=f"D{ci + 1:02d}-{pj + 1:02d}",
                        start_date=date_from,
                        end_date=None,
                        notes="Demo seed for existing TT users",
                        report_visibility="all_assigned",
                        project_type="time_and_materials",
                        currency=client_currency,
                        billable_rate_type=None,
                        project_billable_rate_amount=_billable_rate_amount_for_currency(client_currency, rnd),
                        budget_type=None,
                        budget_amount=None,
                        progress_budget_amount=None,
                        budget_hours=None,
                        budget_resets_every_month=False,
                        budget_includes_expenses=False,
                        send_budget_alerts=False,
                        budget_alert_threshold_percent=None,
                        fixed_fee_amount=None,
                        is_archived=False,
                        created_at=now,
                        updated_at=None,
                    )
                )

        await session.flush()

        for pid in project_ids:
            await seed_default_common_tasks_for_project(session, pid)

        await seed_default_expense_categories_for_all_clients(session)

        proj_repo = ClientProjectRepository(session)
        access_repo = UserProjectAccessRepository(session)
        granter = auth_ids[0]
        for pid in project_ids:
            for uid in auth_ids:
                await access_repo.grant_access_if_absent(
                    uid, pid, granted_by_auth_user_id=granter, projects=proj_repo
                )

        await session.flush()

        task_map = await _task_ids_for_projects(session, project_ids)
        entry_repo = TimeEntryRepository(session)

        total_entries = 0
        for uid in auth_ids:
            picked_days = rnd.sample(weekdays, k=min(n_days_user, len(weekdays)))
            for wd in sorted(picked_days):
                pid = rnd.choice(project_ids)
                tids = task_map.get(pid) or []
                tid = rnd.choice(tids) if tids else None
                sec = rnd.randint(4, 28) * 900
                try:
                    await entry_repo.create(
                        entry_id=str(uuid.uuid4()),
                        auth_user_id=uid,
                        work_date=wd,
                        duration_seconds=sec,
                        hours=None,
                        is_billable=rnd.random() > 0.08,
                        project_id=pid,
                        task_id=tid,
                        description=f"Demo entry user={uid} {wd.isoformat()}",
                        external_reference_url=None,
                    )
                    total_entries += 1
                except ValueError:
                    continue

        await session.commit()

    print(
        f"\nГотово: клиентов={clients_count}, проектов={len(project_ids)}, "
        f"записей времени={total_entries}, пользователей TT={len(auth_ids)}."
    )
    print(f"Удаление демо-клиентов: python scripts/delete_mock_clients.py --prefix {prefix!r} --execute")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Сидирование демо-клиентов/проектов/времени для существующих пользователей TT.")
    p.add_argument("--lite", action="store_true", help="Меньше клиентов, проектов и записей.")
    p.add_argument("--force", action="store_true", help="Разрешить запись, если уже есть клиенты с этим префиксом.")
    p.add_argument("--client-prefix", type=str, default="[demo]", help="Префикс имени клиента (ilike).")
    p.add_argument("--clients", type=int, default=12, help="Число новых клиентов.")
    p.add_argument("--projects-min", type=int, default=4)
    p.add_argument("--projects-max", type=int, default=10)
    p.add_argument("--months-back", type=int, default=4, help="Глубина периода для будничных дат записей.")
    p.add_argument(
        "--entry-weekdays-per-user",
        type=int,
        default=None,
        metavar="N",
        help="Сколько разных будней с записями на каждого пользователя (по умолчанию от размера периода).",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--no-ensure-hourly-rates",
        action="store_true",
        help="Не добавлять недостающие ставки billable/cost по валютам.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = p.parse_args()

    return asyncio.run(
        _run(
            dry_run=bool(args.dry_run),
            force=bool(args.force),
            lite=bool(args.lite),
            client_prefix=args.client_prefix,
            clients_count=max(1, min(80, int(args.clients))),
            projects_min=max(1, min(40, int(args.projects_min))),
            projects_max=max(1, min(80, int(args.projects_max))),
            months_back=max(1, min(24, int(args.months_back))),
            entry_weekdays_per_user=args.entry_weekdays_per_user,
            seed=int(args.seed),
            ensure_hourly_rates=not bool(args.no_ensure_hourly_rates),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
