from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_REPO_ROOT = Path(__file__).resolve().parents[2]

MOCK_CURRENCIES: tuple[str, ...] = ("UZS", "USD", "EUR")

from sqlalchemy import and_, func, select

from application.client_expense_category_defaults import seed_default_expense_categories_for_all_clients
from application.client_task_defaults import seed_default_common_tasks_for_project
from application.demo_seed_budget import demo_budget_fields_for_project
from application.partner_report_confirmation_service import list_partner_auth_user_ids_for_project
from application.weekly_period import saturday_start_of_reporting_week, work_week_start_end_inclusive
from infrastructure.database import async_session_factory
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeManagerClientTaskModel,
    TimeTrackingUserModel,
)
from infrastructure.repository_access import UserProjectAccessRepository
from infrastructure.repository_clients import ClientProjectRepository
from infrastructure.repository_entries import TimeEntryRepository
from infrastructure.repository_partner_report_confirmations import PartnerReportConfirmationRepository
from infrastructure.repository_rates import HourlyRateRepository
from infrastructure.repository_reports import ReportSavedViewRepository, ReportSnapshotRepository
from infrastructure.repository_shared import _now_utc
from infrastructure.repository_weekly_submissions import WeeklySubmissionRepository


def _billable_rate_amount_for_currency(cur: str, rnd: random.Random) -> Decimal:
    u = cur.upper().strip()[:10]
    if u == "UZS":
        return Decimal(str(rnd.randint(1_800_000, 4_500_000)))
    if u == "EUR":
        return Decimal(str(115 + rnd.randint(0, 55)))
    return Decimal(str(130 + rnd.randint(0, 70)))


def _cost_rate_amount_for_currency(billable: Decimal) -> Decimal:
    return (billable * Decimal("0.38")).quantize(Decimal("0.0001"))


async def _pick_sample_projects_one_per_currency(
    session,
    project_ids: list[str],
) -> list[str]:
    """По одному проекту на каждую валюту из MOCK_CURRENCIES (если есть)."""
    picked: list[str] = []
    need = set(MOCK_CURRENCIES)
    for pid in project_ids:
        if not need:
            break
        row = await session.get(TimeManagerClientProjectModel, pid)
        if not row:
            continue
        c = (row.currency or "USD").strip().upper()[:10]
        if c in need:
            picked.append(pid)
            need.remove(c)
    return picked


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


def _async_pg(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def _seed_mock_invoices(
    session,
    *,
    rnd: random.Random,
    actor: int,
    prefix: str,
    project_ids: list[str],
    date_from: date,
    date_to: date,
    lite: bool,
) -> int:
    """Черновик счёта без project_id и строки только времени через patch — без проверки партнёрского периода."""
    from fastapi import HTTPException

    from application.invoice_service import create_invoice, mark_viewed, patch_invoice_draft, send_invoice

    target = min(len(project_ids) // 3, 8 if lite else 55)
    if target < 1:
        return 0

    shuffled = list(project_ids)
    rnd.shuffle(shuffled)
    used_entries: set[str] = set()
    created = 0
    status_cycle = 0

    for pid in shuffled:
        if created >= target:
            break
        proj_row = await session.get(TimeManagerClientProjectModel, pid)
        if not proj_row:
            continue
        conds = [
            TimeEntryModel.project_id == pid,
            TimeEntryModel.voided_at.is_(None),
            TimeEntryModel.is_billable.is_(True),
        ]
        if used_entries:
            conds.append(TimeEntryModel.id.not_in(used_entries))
        q = (
            select(TimeEntryModel.id)
            .where(and_(*conds))
            .order_by(TimeEntryModel.work_date.asc())
            .limit(5)
        )
        entry_ids = [str(x) for x in (await session.execute(q)).scalars().all()]
        if len(entry_ids) < 2:
            continue
        cid = str(proj_row.client_id)
        issue_d = max(date_from, date_to - timedelta(days=45))
        due_d = date_to + timedelta(days=30)
        try:
            inv = await create_invoice(
                session,
                actor_auth_user_id=actor,
                client_id=cid,
                project_id=None,
                issue_date=issue_d,
                due_date=due_d,
                currency=str(proj_row.currency or "USD").strip().upper()[:10],
                tax_percent=None,
                tax2_percent=None,
                discount_percent=None,
                client_note=f"{prefix} mock invoice",
                internal_note=None,
                lines=[
                    {
                        "description": "[mock] placeholder — будет заменён строками времени",
                        "quantity": "1",
                        "unitAmount": "0.01",
                    }
                ],
                time_entry_ids=None,
                expense_ids=None,
                partner_billing_period_from=None,
                partner_billing_period_to=None,
            )
            await session.flush()
            replace_lines = [
                {"lineKind": "time", "timeEntryId": eid, "time_entry_id": eid} for eid in entry_ids
            ]
            inv = await patch_invoice_draft(
                session,
                inv,
                actor_auth_user_id=actor,
                project_id=pid,
                replace_lines=replace_lines,
            )
            used_entries.update(entry_ids)
            status_cycle += 1
            rem = status_cycle % 4
            if rem != 0:
                await send_invoice(session, inv, actor_auth_user_id=actor)
            if rem in (2, 3):
                await mark_viewed(session, inv, actor_auth_user_id=actor)
            created += 1
        except HTTPException as exc:
            print(f"[invoices] пропуск проекта {pid[:8]}…: {exc.detail!r}", file=sys.stderr)

    return created


async def _seed_expenses_database(
    *,
    expenses_db_url: str,
    project_ids: list[str],
    creator_uid: int,
    date_from: date,
    date_to: date,
    rnd: random.Random,
    per_project: int,
    lite: bool,
) -> int:
    """Прямая вставка в БД сервиса expenses (TT потом читает через EXPENSES_SERVICE_URL)."""
    exp_root = _REPO_ROOT / "expenses"
    if not exp_root.is_dir():
        print("[expenses] каталог expenses/ не найден — пропуск.", file=sys.stderr)
        return 0

    sys.path.insert(0, str(exp_root))
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from infrastructure.repositories import ExpenseRepository  # noqa: PLC0415
    except ImportError as exc:
        print(f"[expenses] не удалось импортировать модуль expenses: {exc}", file=sys.stderr)
        return 0

    n_proj_sample = min(len(project_ids), 25 if lite else len(project_ids))
    picked = rnd.sample(project_ids, k=n_proj_sample) if len(project_ids) > n_proj_sample else list(project_ids)
    pp = max(1, min(12, per_project))
    if lite:
        pp = min(pp, 2)

    engine = create_async_engine(_async_pg(expenses_db_url), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    inserted = 0
    try:
        async with factory() as esession:
            repo = ExpenseRepository(esession)
            span = max(1, (date_to - date_from).days)
            for pid in picked:
                for _ in range(pp):
                    exp_day = date_from + timedelta(days=rnd.randint(0, span - 1))
                    amt_uzs = Decimal(str(rnd.randint(80_000, 950_000)))
                    if rnd.random() < 0.45:
                        eq = amt_uzs
                        xr = Decimal("1")
                    else:
                        fx = Decimal(str(11000 + rnd.randint(-800, 800)))
                        eq = (amt_uzs / fx).quantize(Decimal("0.01"))
                        xr = (amt_uzs / eq).quantize(Decimal("0.000001")) if eq else Decimal("1")
                    eid = "m" + uuid.uuid4().hex[:12]
                    await repo.create(
                        id_=eid,
                        description=f"[mock] расход для отчёта (проект {pid[:8]}…)",
                        expense_date=exp_day,
                        payment_deadline=None,
                        amount_uzs=amt_uzs,
                        exchange_rate=xr,
                        equivalent_amount=eq,
                        expense_type=rnd.choice(["transport", "services", "client_expense", "purchase"]),
                        expense_subtype=None,
                        is_reimbursable=True,
                        payment_method="card",
                        department_id=None,
                        project_id=pid,
                        expense_category_id=None,
                        vendor=f"Mock vendor {rnd.randint(1, 200)}",
                        business_purpose="Данные для отчёта TT",
                        comment=None,
                        status="approved",
                        created_by_user_id=creator_uid,
                        updated_by_user_id=creator_uid,
                    )
                    inserted += 1
            await esession.commit()
    finally:
        await engine.dispose()
        try:
            sys.path.remove(str(exp_root))
        except ValueError:
            pass
    return inserted


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
    with_expenses: bool,
    expenses_database_url: str | None,
    expenses_per_project: int,
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
        f"План (seed={seed}): валюты клиентов/проектов={','.join(MOCK_CURRENCIES)}; "
        f"пользователей TT={n_users}, auth_user_id с {auth_ids[0]} по {auth_ids[-1]}, "
        f"клиентов≈{n_clients}, проектов≈{est_projects}, доступов≈{est_access}, "
        f"записей времени≈{est_entries}, период {date_from} … {date_to}."
    )

    if dry_run:
        print("\n[dry-run] БД не изменена. Для записи: --execute [--force если уже есть мок-клиенты].")
        if with_expenses:
            eu = (expenses_database_url or "").strip()
            if eu:
                print(f"  --with-expenses: будет использован EXPENSES_DATABASE_URL ({eu[:48]}…)")
            else:
                print(
                    "  --with-expenses: задайте EXPENSES_DATABASE_URL или --expenses-database-url "
                    "(иначе расходы для отчёта TT не создаются).",
                    file=sys.stderr,
                )
        return 0

    bundle: dict[str, object] = {}

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
            for cur in MOCK_CURRENCIES:
                bill = _billable_rate_amount_for_currency(cur, rnd)
                await rate_repo.create(
                    auth_user_id=uid,
                    rate_kind="billable",
                    amount=bill,
                    currency=cur,
                    valid_from=None,
                    valid_to=None,
                    applies_to_project_id=None,
                )
                await rate_repo.create(
                    auth_user_id=uid,
                    rate_kind="cost",
                    amount=_cost_rate_amount_for_currency(bill),
                    currency=cur,
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
                        currency=client_currency,
                        billable_rate_type=None,
                        project_billable_rate_amount=_billable_rate_amount_for_currency(client_currency, rnd),
                        **demo_budget_fields_for_project(client_currency, rnd, slot=ci * 100 + pj),
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
        n_invoices = await _seed_mock_invoices(
            session,
            rnd=rnd,
            actor=actor,
            prefix=prefix,
            project_ids=project_ids,
            date_from=date_from,
            date_to=date_to,
            lite=lite,
        )

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
        await saved_repo.create(
            name=f"{prefix} expense report view",
            owner_user_id=actor,
            filters={
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
                "reportType": "detailed-expense",
            },
        )

        snap_repo = ReportSnapshotRepository(session)
        time_snap_ids: list[str] = []

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
            time_snap_ids.append(snap.id)

        exp_proj_pick = await _pick_sample_projects_one_per_currency(session, project_ids)
        if exp_proj_pick:
            rows_exp = [
                {"source_type": "project", "source_id": p, "data": {"projectId": p}} for p in exp_proj_pick
            ]
            await snap_repo.create(
                name=f"{prefix} expense snapshot (mock)",
                report_type="detailed-expense",
                group_by="projects",
                filters={
                    "dateFrom": date_from.isoformat(),
                    "dateTo": date_to.isoformat(),
                    "projectIds": exp_proj_pick,
                },
                created_by_user_id=actor,
                rows_data=rows_exp,
            )

        if not skip_confirmations and project_ids and time_snap_ids:
            conf_repo = PartnerReportConfirmationRepository(session)
            p0 = project_ids[0]
            p1 = project_ids[min(5, len(project_ids) - 1)]

            df0, dt0 = work_week_start_end_inclusive(date_from + timedelta(days=10))
            pend = await conf_repo.upsert_submit(
                snapshot_id=time_snap_ids[0],
                project_id=p0,
                date_from=df0,
                date_to=dt0,
                title=f"{prefix} pending confirmation",
                submitted_by_auth_user_id=actor,
            )

            df1, dt1 = work_week_start_end_inclusive(date_from + timedelta(days=24))
            req_row = await conf_repo.upsert_submit(
                snapshot_id=time_snap_ids[-1],
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

        bundle.update(
            project_ids=list(project_ids),
            client_count=len(client_ids),
            project_count=len(project_ids),
            invoice_count=n_invoices,
        )

        await session.commit()

    exp_n = 0
    exp_url_eff = (expenses_database_url or "").strip()
    if with_expenses and exp_url_eff and bundle.get("project_ids"):
        exp_n = await _seed_expenses_database(
            expenses_db_url=exp_url_eff,
            project_ids=list(bundle["project_ids"]),  # type: ignore[arg-type]
            creator_uid=first_auth_user_id,
            date_from=date_from,
            date_to=date_to,
            rnd=rnd,
            per_project=expenses_per_project,
            lite=lite,
        )

    if with_expenses and not exp_url_eff:
        print(
            "[expenses] указан --with-expenses, но нет URL БД (EXPENSES_DATABASE_URL или --expenses-database-url).",
            file=sys.stderr,
        )

    cc = int(bundle.get("client_count", 0) or 0)
    pc = int(bundle.get("project_count", 0) or 0)
    invc = int(bundle.get("invoice_count", 0) or 0)
    print(
        f"\nГотово: {n_users} пользователей TT, {cc} клиентов ({','.join(MOCK_CURRENCIES)} по очереди), "
        f"{pc} проектов, счетов≈{invc}, строк расходов в expenses={exp_n} "
        f"(отчёт TT их видит при EXPENSES_SERVICE_URL на сервис expenses)."
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
    p.add_argument(
        "--with-expenses",
        action="store_true",
        help="После TT записать расходы в БД сервиса expenses (нужен EXPENSES_DATABASE_URL или флаг URL ниже).",
    )
    p.add_argument(
        "--expenses-database-url",
        type=str,
        default="",
        metavar="URL",
        help="Строка PostgreSQL для БД expenses; если пусто — берётся из переменной окружения EXPENSES_DATABASE_URL.",
    )
    p.add_argument(
        "--expenses-per-project",
        type=int,
        default=4,
        help="Число строк расходов на каждый выбранный проект (выбирается подвыборка проектов).",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = p.parse_args()
    exp_url_merged = (args.expenses_database_url or "").strip() or os.environ.get(
        "EXPENSES_DATABASE_URL", ""
    ).strip()

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
            with_expenses=bool(args.with_expenses),
            expenses_database_url=exp_url_merged or None,
            expenses_per_project=max(1, min(30, int(args.expenses_per_project))),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
