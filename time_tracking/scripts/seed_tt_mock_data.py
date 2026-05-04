"""Насыщение БД time_tracking тестовыми данными (dev/staging).

Создаёт:
  • пользователей TT с синтетическими auth_user_id (нет записей в auth — только для локальных проверок UI);
  • ставки billable/cost;
  • клиентов с префиксом имени (по умолчанию «[mock]» — удаление: scripts/delete_mock_clients.py);
  • у каждого клиента случайное число проектов (диапазон задаётся флагами);
  • задачи по умолчанию на проект + категории расходов на клиента;
  • доступ всех мок-пользователей ко всем мок-проектам;
  • записи времени за несколько месяцев (будни, случайная длительность);
  • недельные сдачи (status submitted);
  • сохранённое представление отчёта и несколько снимков отчётов;
  • запросы партнёрского подтверждения: часть в pending, часть fully_confirmed (подписи всех партнёров).

Расходы (expense requests) в таблицах TT не живут — отчёт «расходы» строится по HTTP из сервиса expenses.
Для полного контура расходов нужен отдельный сид/данные в expenses.

Запуск из каталога time_tracking (или из корня репо с PYTHONPATH=time_tracking):

  python scripts/seed_tt_mock_data.py --dry-run
  python scripts/seed_tt_mock_data.py --execute

Docker (рабочая директория /app):

  python scripts/seed_tt_mock_data.py --execute --lite
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

from sqlalchemy import func, select

from application.client_expense_category_defaults import seed_default_expense_categories_for_all_clients
from application.client_task_defaults import seed_default_common_tasks_for_project
from application.partner_report_confirmation_service import list_partner_auth_user_ids_for_project
from application.weekly_period import saturday_start_of_reporting_week, work_week_start_end_inclusive
from infrastructure.database import async_session_factory
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeManagerClientTaskModel,
    TimeTrackingUserModel,
    TimeTrackingUserProjectAccessModel,
)
from infrastructure.repository_access import UserProjectAccessRepository
from infrastructure.repository_clients import ClientProjectRepository
from infrastructure.repository_entries import TimeEntryRepository
from infrastructure.repository_partner_report_confirmations import PartnerReportConfirmationRepository
from infrastructure.repository_rates import HourlyRateRepository
from infrastructure.repository_reports import ReportSavedViewRepository, ReportSnapshotRepository
from infrastructure.repository_shared import _now_utc
from infrastructure.repository_weekly_submissions import WeeklySubmissionRepository


def _week_starts_in_range(d0: date, d1: date) -> list[date]:
    seen: set[date] = set()
    d = d0
    while d <= d1:
        seen.add(saturday_start_of_reporting_week(d))
        d += timedelta(days=1)
    return sorted(seen)


def _weekdays_in_range(d0: date, d1: date) -> list[date]:
    out: list[date] = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


async def _preflight(session, *, prefix: str, auth_ids: list[int]) -> tuple[int, int]:
    occupied = (
        await session.execute(
            select(func.count()).select_from(TimeTrackingUserModel).where(
                TimeTrackingUserModel.auth_user_id.in_(auth_ids)
            )
        )
    ).scalar_one()
    occ_i = int(occupied or 0)
    existing_clients = (
        await session.execute(
            select(func.count())
            .select_from(TimeManagerClientModel)
            .where(TimeManagerClientModel.name.ilike(f"{prefix.strip()}%"))
        )
    ).scalar_one()
    return occ_i, int(existing_clients or 0)


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


async def _run(
    *,
    dry_run: bool,
    force: bool,
    lite: bool,
    client_prefix: str,
    first_auth_user_id: int,
    n_users: int,
    n_clients: int,
    projects_min: int,
    projects_max: int,
    months_back: int,
    entry_probability: float,
    seed: int,
    skip_confirmations: bool,
) -> int:
    rnd = random.Random(seed)
    prefix = client_prefix.strip() or "[mock]"

    if lite:
        n_users = min(n_users, 6)
        n_clients = min(n_clients, 5)
        projects_min = min(projects_min, 3)
        projects_max = min(max(projects_max, projects_min), 8)
        months_back = min(months_back, 3)
        entry_probability = min(entry_probability, 0.35)

    if projects_max < projects_min:
        projects_max = projects_min

    auth_ids = [first_auth_user_id + i for i in range(n_users)]
    today = date.today()
    date_from = today.replace(day=1) - timedelta(days=30 * (months_back - 1))
    date_from = date_from.replace(day=1)
    date_to = today

    est_projects = n_clients * (projects_min + projects_max) // 2
    est_access = est_projects * n_users
    weekdays_n = len(_weekdays_in_range(date_from, date_to))
    est_entries = int(weekdays_n * n_users * entry_probability)

    print(
        f"План (seed={seed}): пользователей TT={n_users}, auth_user_id с {auth_ids[0]} по {auth_ids[-1]}, "
        f"клиентов≈{n_clients}, проектов≈{est_projects}, доступов≈{est_access}, "
        f"записей времени≈{est_entries}, период {date_from} … {date_to}."
    )

    if dry_run:
        print("\n[dry-run] БД не изменена. Для записи: --execute [--force если уже есть мок-клиенты].")
        return 0

    async with async_session_factory() as session:
        occ_users, n_exist_clients = await _preflight(session, prefix=prefix, auth_ids=auth_ids)
        if occ_users:
            print(
                f"Ошибка: в TT уже есть пользователи с auth_user_id из диапазона ({occ_users} совпадений). "
                "Смените --first-auth-user-id или удалите строки.",
                file=sys.stderr,
            )
            return 1
        if n_exist_clients and not force:
            print(
                f"Ошибка: уже есть клиенты с именем ilike {prefix!r}%. "
                "Удалите их (delete_mock_clients.py) или передайте --force.",
                file=sys.stderr,
            )
            return 1

        now = _now_utc()

        partner_positions = {"Partner (mock)", "Партнёр (mock)"}
        for i, uid in enumerate(auth_ids):
            pos = list(partner_positions)[i % 2] if i < 2 else "Associate (mock)"
            session.add(
                TimeTrackingUserModel(
                    auth_user_id=uid,
                    email=f"mock.tt.user.{uid}@local.invalid",
                    display_name=f"Mock User {uid}",
                    picture=None,
                    position=pos,
                    role="",
                    is_blocked=False,
                    is_archived=False,
                    weekly_capacity_hours=Decimal("35"),
                    reports_to_auth_user_id=None,
                    created_at=now,
                    updated_at=None,
                )
            )

        rate_repo = HourlyRateRepository(session)
        for uid in auth_ids:
            await rate_repo.create(
                auth_user_id=uid,
                rate_kind="billable",
                amount=Decimal(str(120 + rnd.randint(0, 80))),
                currency="USD",
                valid_from=None,
                valid_to=None,
                applies_to_project_id=None,
            )
            await rate_repo.create(
                auth_user_id=uid,
                rate_kind="cost",
                amount=Decimal(str(40 + rnd.randint(0, 30))),
                currency="USD",
                valid_from=None,
                valid_to=None,
                applies_to_project_id=None,
            )

        await session.flush()

        client_ids: list[str] = []
        project_ids: list[str] = []
        projects_by_client: dict[str, list[str]] = {}

        for ci in range(n_clients):
            cid = str(uuid.uuid4())
            client_ids.append(cid)
            session.add(
                TimeManagerClientModel(
                    id=cid,
                    name=f"{prefix} Client {ci + 1:02d}",
                    address=None,
                    currency="USD",
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
            plist: list[str] = []
            for pj in range(np):
                pid = str(uuid.uuid4())
                project_ids.append(pid)
                plist.append(pid)
                session.add(
                    TimeManagerClientProjectModel(
                        id=pid,
                        client_id=cid,
                        name=f"Project {ci + 1:02d}-{pj + 1:02d}",
                        code=f"P{ci + 1:02d}-{pj + 1:02d}",
                        start_date=date_from,
                        end_date=None,
                        notes="Mock project",
                        report_visibility="all_assigned",
                        project_type="time_and_materials",
                        currency="USD",
                        billable_rate_type=None,
                        project_billable_rate_amount=Decimal("150"),
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
            projects_by_client[cid] = plist

        await session.flush()

        for pid in project_ids:
            await seed_default_common_tasks_for_project(session, pid)

        await seed_default_expense_categories_for_all_clients(session)

        proj_repo = ClientProjectRepository(session)
        access_repo = UserProjectAccessRepository(session)
        granted_by = auth_ids[0]
        for pid in project_ids:
            for uid in auth_ids:
                await access_repo.grant_access_if_absent(
                    uid, pid, granted_by_auth_user_id=granted_by, projects=proj_repo
                )

        await session.flush()

        task_map = await _task_ids_for_projects(session, project_ids)
        entry_repo = TimeEntryRepository(session)
        weekdays = _weekdays_in_range(date_from, date_to)

        for uid in auth_ids:
            for wd in weekdays:
                if rnd.random() > entry_probability:
                    continue
                pid = rnd.choice(project_ids)
                tids = task_map.get(pid) or []
                tid = rnd.choice(tids) if tids else None
                sec = rnd.randint(4, 32) * 900
                try:
                    await entry_repo.create(
                        entry_id=str(uuid.uuid4()),
                        auth_user_id=uid,
                        work_date=wd,
                        duration_seconds=sec,
                        hours=None,
                        is_billable=rnd.random() > 0.12,
                        project_id=pid,
                        task_id=tid,
                        description=f"Mock entry {wd.isoformat()}",
                        external_reference_url=None,
                    )
                except ValueError:
                    continue

        wrepo = WeeklySubmissionRepository(session)
        week_starts = _week_starts_in_range(date_from, date_to)
        for uid in auth_ids:
            for ws in week_starts:
                we = ws + timedelta(days=6)
                await wrepo.upsert_submission(
                    auth_user_id=uid, week_start=ws, week_end=we, auto=False
                )

        actor = auth_ids[0]
        saved_repo = ReportSavedViewRepository(session)
        await saved_repo.create(
            name=f"{prefix} saved view",
            owner_user_id=actor,
            filters={
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
                "reportType": "time",
            },
        )

        snap_repo = ReportSnapshotRepository(session)
        snap_ids: list[str] = []

        chunk = max(1, len(project_ids) // 4)
        sample_projects = (
            project_ids[:chunk],
            project_ids[chunk : chunk * 2],
            project_ids[-min(chunk * 2, len(project_ids)) :],
        )
        for idx, group in enumerate(sample_projects):
            group = [p for p in group if p]
            if not group:
                continue
            rows_data = [
                {
                    "source_type": "project",
                    "source_id": p,
                    "data": {"projectId": p, "label": f"mock row {p[:8]}"},
                }
                for p in group[: min(12, len(group))]
            ]
            snap = await snap_repo.create(
                name=f"{prefix} snapshot {idx + 1}",
                report_type="time",
                group_by="projects",
                filters={
                    "dateFrom": date_from.isoformat(),
                    "dateTo": date_to.isoformat(),
                    "projectIds": [r["source_id"] for r in rows_data],
                },
                created_by_user_id=actor,
                rows_data=rows_data,
            )
            snap_ids.append(snap.id)

        if not skip_confirmations and project_ids and snap_ids:
            conf_repo = PartnerReportConfirmationRepository(session)
            p0 = project_ids[0]
            p1 = project_ids[min(5, len(project_ids) - 1)]

            df0, dt0 = work_week_start_end_inclusive(date_from + timedelta(days=10))
            pend = await conf_repo.upsert_submit(
                snapshot_id=snap_ids[0],
                project_id=p0,
                date_from=df0,
                date_to=dt0,
                title=f"{prefix} pending confirmation",
                submitted_by_auth_user_id=actor,
            )

            df1, dt1 = work_week_start_end_inclusive(date_from + timedelta(days=24))
            req_row = await conf_repo.upsert_submit(
                snapshot_id=snap_ids[-1],
                project_id=p1,
                date_from=df1,
                date_to=dt1,
                title=f"{prefix} confirmed confirmation",
                submitted_by_auth_user_id=actor,
            )

            partners = await list_partner_auth_user_ids_for_project(
                session, access_repo, p1, authorization=None
            )
            if partners:
                for puid in partners:
                    if not await conf_repo.partner_has_signed(req_row.id, puid):
                        await conf_repo.add_signature(req_row.id, puid)
                await conf_repo.mark_fully_confirmed(req_row.id)
            else:
                print(
                    "\nПредупреждение: для fully_confirmed не найдены партнёры по должности — "
                    "запрос оставлен без подписей (проверьте position «Partner» у пользователей).",
                    file=sys.stderr,
                )

            await session.flush()
            print(
                f"\nПартнёрские подтверждения: pending id={pend.id}, "
                f"запрос для подтверждённого сценария id={req_row.id} "
                f"(партнёров: {len(partners)})."
            )

        await session.commit()

    print(
        f"\nГотово: {n_users} пользователей, {len(client_ids)} клиентов, "
        f"{len(project_ids)} проектов, записи времени и отчёты созданы."
    )
    print(f"Удаление мок-клиентов: python scripts/delete_mock_clients.py --prefix {prefix!r} --execute")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Сидирование mock-данных time tracking.")
    p.add_argument("--lite", action="store_true", help="Меньше объёмов для быстрой проверки.")
    p.add_argument("--force", action="store_true", help="Разрешить execute при уже существующих клиентах с префиксом.")
    p.add_argument("--client-prefix", type=str, default="[mock]", help="Префикс имени клиента.")
    p.add_argument("--first-auth-user-id", type=int, default=920_001, metavar="N")
    p.add_argument("--users", type=int, default=12)
    p.add_argument("--clients", type=int, default=25)
    p.add_argument("--projects-min", type=int, default=10)
    p.add_argument("--projects-max", type=int, default=30)
    p.add_argument("--months-back", type=int, default=5)
    p.add_argument(
        "--entry-probability",
        type=float,
        default=0.55,
        metavar="P",
        help="Вероятность записи на каждый будний день для каждого пользователя (0..1).",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--skip-confirmations",
        action="store_true",
        help="Не создавать партнёрские подтверждения отчётов.",
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
            first_auth_user_id=int(args.first_auth_user_id),
            n_users=max(1, int(args.users)),
            n_clients=max(1, min(80, int(args.clients))),
            projects_min=max(1, min(50, int(args.projects_min))),
            projects_max=max(1, min(80, int(args.projects_max))),
            months_back=max(1, min(24, int(args.months_back))),
            entry_probability=max(0.0, min(1.0, float(args.entry_probability))),
            seed=int(args.seed),
            skip_confirmations=bool(args.skip_confirmations),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
