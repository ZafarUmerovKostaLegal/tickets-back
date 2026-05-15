"""Очистка всех прикладных данных с сохранением только пользователей Time Tracking (не мок).

Делает:
  • БД time tracking — удаляет клиентов, записи времени, счета, отчёты и т.д.; строки
    time_tracking_users для реальных сотрудников остаются; мок-пользователей TT
    (email вида mock.tt.user.*@local.invalid) удаляет из TT.
  • БД auth — удаляет пользователей users, у которых нет строки в time_tracking_users
    (по списку auth_user_id из TT после шага выше). Остальные таблицы auth (roles и т.д.) не трогает.
  • Остальные сервисы (tickets, todos, notifications, inventory, attendance, expenses, vacation)
    — по отдельным URL из env; если URL не задан, шаг пропускается.

Мок-клиенты/проекты seed_tt_mock_data после полного сброса TT уже не существуют; мок-пользователи
удаляются из TT и из auth.

Контейнер time_tracking (Portainer → Console): после пересборки образа скрипт и копии модулей в /app/wipe_repo/:

  python /app/wipe_repo/scripts/wipe_all_keep_tt_users.py --dry-run

  Переменные AUTH_DATABASE_URL, TIME_TRACKING_DATABASE_URL (или DATABASE_URL для TT) и прочие
  задайте в стеке / env контейнера (в docker-compose для time_tracking они проброшены по умолчанию).

Запуск на сервере через venv (клон репозитория):

  cd /path/to/tickets-back
  python3 -m venv .venv-wipe && source .venv-wipe/bin/activate   # Windows: .venv-wipe\\Scripts\\activate
  pip install -r scripts/requirements-wipe.txt

  export AUTH_DATABASE_URL="postgresql://..."
  export TIME_TRACKING_DATABASE_URL="postgresql://..."
  # при необходимости: TICKETS_DATABASE_URL, TODOS_DATABASE_URL, …

  python scripts/wipe_all_keep_tt_users.py --dry-run
  python scripts/wipe_all_keep_tt_users.py --execute --confirm WIPE_KEEP_TT_ONLY

Альтернатива — Docker (профиль tools), если удобнее не ставить venv на сервере:

  docker compose --profile tools run --rm wipe_keep_tt --dry-run

Опционально задайте: TICKETS_DATABASE_URL, TODOS_DATABASE_URL, NOTIFICATIONS_DATABASE_URL,
INVENTORY_DATABASE_URL, ATTENDANCE_DATABASE_URL, EXPENSES_DATABASE_URL, VACATION_DATABASE_URL.

Если после удаления моков в TT не останется ни одного auth_user_id, удаление из auth не выполняется
(защита), пока не передан флаг --delete-all-auth-without-tt.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_REPO_ROOT = Path(__file__).resolve().parents[1]

CONFIRM_PHRASE = "WIPE_KEEP_TT_ONLY"

# --- URL helper ---


def make_async_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise ValueError("Пустой URL БД")
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+asyncpg://", 1)
    return u


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


# --- Time tracking ---


async def _tt_non_mock_auth_ids(tt_url: str) -> list[int]:
    """auth_user_id из time_tracking_users, кроме мок-email seed_tt_mock_data."""

    sys.path.insert(0, str(_REPO_ROOT / "time_tracking"))
    try:
        from infrastructure.models import TimeTrackingUserModel as TU  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "time_tracking"))
        except ValueError:
            pass

    mock_email = TU.email.like("mock.tt.user.%@local.invalid")
    engine = create_async_engine(make_async_url(tt_url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fac() as s:
            r = await s.execute(select(TU.auth_user_id).where(~mock_email))
            return sorted({int(x[0]) for x in r.all()})
    finally:
        await engine.dispose()


async def _wipe_time_tracking(tt_url: str, *, dry_run: bool) -> None:
    sys.path.insert(0, str(_REPO_ROOT / "time_tracking"))
    try:
        from infrastructure.models import (  # noqa: PLC0415
            TimeEntryModel,
            TimeEntryEditUnlockModel,
            TimeManagerClientModel,
            TimeTrackingUserModel,
            TimeTrackingUserProjectAccessModel,
            UserHourlyRateModel,
            WeeklyTimeSubmissionModel,
        )
        from infrastructure.models_invoices import InvoiceCounterModel, InvoiceModel  # noqa: PLC0415
        from infrastructure.models_reports import ReportSavedViewModel, ReportSnapshotModel  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "time_tracking"))
        except ValueError:
            pass

    mock_email_cond = TimeTrackingUserModel.email.like("mock.tt.user.%@local.invalid")
    engine = create_async_engine(make_async_url(tt_url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        r_keep = await s.execute(
            select(TimeTrackingUserModel.auth_user_id, TimeTrackingUserModel.email).where(~mock_email_cond)
        )
        keep_rows = list(r_keep.all())
        r_mock = await s.execute(
            select(TimeTrackingUserModel.auth_user_id).where(mock_email_cond)
        )
        mock_uids = [int(x[0]) for x in r_mock.all()]

        print(f"[time_tracking] Пользователей TT (не мок): {len(keep_rows)}")
        for uid, em in keep_rows[:30]:
            print(f"  остаётся auth_user_id={uid}  {em!r}")
        if len(keep_rows) > 30:
            print(f"  … ещё: {len(keep_rows) - 30}")
        print(f"[time_tracking] Мок-пользователей TT к удалению из TT/auth: {len(mock_uids)}")

        if dry_run:
            print("[time_tracking] dry-run — данные TT не трогаем.")
            return

        await s.execute(delete(TimeEntryModel))
        await s.execute(delete(InvoiceModel))
        await s.execute(delete(InvoiceCounterModel))
        await s.execute(delete(ReportSnapshotModel))
        await s.execute(delete(ReportSavedViewModel))
        await s.execute(delete(WeeklyTimeSubmissionModel))
        await s.execute(delete(TimeEntryEditUnlockModel))
        await s.execute(delete(TimeTrackingUserProjectAccessModel))
        await s.execute(delete(UserHourlyRateModel))
        await s.execute(delete(TimeManagerClientModel))
        await s.execute(delete(TimeTrackingUserModel).where(mock_email_cond))
        await s.commit()
        print("[time_tracking] Очищены сущности TT; мок-пользователи удалены из time_tracking_users.")


# --- Auth ---


async def _wipe_auth_users(
    auth_url: str,
    keep_auth_ids: list[int],
    *,
    dry_run: bool,
    delete_all_without_tt: bool,
) -> None:
    sys.path.insert(0, str(_REPO_ROOT / "auth"))
    try:
        from infrastructure.models import UserModel  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "auth"))
        except ValueError:
            pass

    engine = create_async_engine(make_async_url(auth_url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        if not keep_auth_ids:
            if not delete_all_without_tt:
                print(
                    "[auth] Нет пользователей TT для сохранения — удаление из users пропущено. "
                    "Нужен флаг --delete-all-auth-without-tt чтобы удалить всех пользователей auth."
                )
                return
            to_remove_clause = True  # delete all users
            r_cnt = await s.execute(select(UserModel))
            n = len(list(r_cnt.scalars().all()))
            print(f"[auth] Удаление всех пользователей users: {n}")
        else:
            r_del = await s.execute(select(UserModel.id).where(~UserModel.id.in_(keep_auth_ids)))
            remove_ids = [int(x[0]) for x in r_del.all()]
            print(f"[auth] Удалить пользователей auth (не из TT): {len(remove_ids)}")
            for i in remove_ids[:40]:
                print(f"  user id={i}")
            if len(remove_ids) > 40:
                print(f"  … ещё: {len(remove_ids) - 40}")
            to_remove_clause: object
            if remove_ids:
                to_remove_clause = UserModel.id.in_(remove_ids)
            else:
                to_remove_clause = None

        if dry_run:
            print("[auth] dry-run — users не изменены.")
            return

        if to_remove_clause is True:
            await s.execute(delete(UserModel))
        elif to_remove_clause is not None:
            await s.execute(delete(UserModel).where(to_remove_clause))
        else:
            print("[auth] Нечего удалять в users.")
        await s.commit()
        print("[auth] Готово.")


# --- Tickets ---


async def _wipe_tickets(url: Optional[str], *, dry_run: bool) -> None:
    if not url:
        print("[tickets] URL не задан — пропуск.")
        return
    sys.path.insert(0, str(_REPO_ROOT / "tickets"))
    try:
        from infrastructure.models import CommentModel, TicketModel  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "tickets"))
        except ValueError:
            pass

    engine = create_async_engine(make_async_url(url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        c = await s.execute(select(TicketModel))
        n = len(list(c.scalars().all()))
        print(f"[tickets] Тикетов: {n}")
        if dry_run:
            return
        await s.execute(delete(CommentModel))
        await s.execute(delete(TicketModel))
        await s.commit()
        print("[tickets] Очищено.")


async def _wipe_todos(url: Optional[str], *, dry_run: bool) -> None:
    if not url:
        print("[todos] URL не задан — пропуск.")
        return
    sys.path.insert(0, str(_REPO_ROOT / "todos"))
    try:
        from infrastructure.models import OutlookCalendarTokenModel, TodoBoardModel  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "todos"))
        except ValueError:
            pass

    engine = create_async_engine(make_async_url(url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        b = await s.execute(select(TodoBoardModel))
        nb = len(list(b.scalars().all()))
        print(f"[todos] Досок: {nb}")
        if dry_run:
            return
        await s.execute(delete(OutlookCalendarTokenModel))
        await s.execute(delete(TodoBoardModel))
        await s.commit()
        print("[todos] Очищено.")


async def _wipe_notifications(url: Optional[str], *, dry_run: bool) -> None:
    if not url:
        print("[notifications] URL не задан — пропуск.")
        return
    sys.path.insert(0, str(_REPO_ROOT / "notifications"))
    try:
        from infrastructure.models import NotificationModel  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "notifications"))
        except ValueError:
            pass

    engine = create_async_engine(make_async_url(url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        n = await s.execute(select(NotificationModel))
        print(f"[notifications] Записей: {len(list(n.scalars().all()))}")
        if dry_run:
            return
        await s.execute(delete(NotificationModel))
        await s.commit()
        print("[notifications] Очищено.")


async def _wipe_inventory(url: Optional[str], *, dry_run: bool) -> None:
    if not url:
        print("[inventory] URL не задан — пропуск.")
        return
    sys.path.insert(0, str(_REPO_ROOT / "inventory"))
    try:
        from infrastructure.models import CategoryModel, InventoryItemModel  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "inventory"))
        except ValueError:
            pass

    engine = create_async_engine(make_async_url(url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        ni = await s.execute(select(InventoryItemModel))
        nc = await s.execute(select(CategoryModel))
        print(
            f"[inventory] Позиций: {len(list(ni.scalars().all()))}, категорий: {len(list(nc.scalars().all()))}"
        )
        if dry_run:
            return
        await s.execute(text("TRUNCATE inventory_items, inventory_categories RESTART IDENTITY CASCADE"))
        await s.commit()
        print("[inventory] Очищено.")


async def _wipe_attendance(url: Optional[str], *, dry_run: bool) -> None:
    if not url:
        print("[attendance] URL не задан — пропуск.")
        return
    sys.path.insert(0, str(_REPO_ROOT / "attendance"))
    try:
        from infrastructure.models import (  # noqa: PLC0415
            AttendanceExplanationModel,
            HikvisionUserBindingModel,
        )
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "attendance"))
        except ValueError:
            pass

    engine = create_async_engine(make_async_url(url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        ne = await s.execute(select(AttendanceExplanationModel))
        nb = await s.execute(select(HikvisionUserBindingModel))
        print(
            f"[attendance] Пояснений: {len(list(ne.scalars().all()))}, "
            f"привязок Hikvision: {len(list(nb.scalars().all()))}"
        )
        if dry_run:
            return
        await s.execute(delete(AttendanceExplanationModel))
        await s.execute(delete(HikvisionUserBindingModel))
        await s.commit()
        print("[attendance] Очищено (настройки рабочего дня attendance_settings не трогаем).")


async def _wipe_expenses(url: Optional[str], *, dry_run: bool) -> None:
    if not url:
        print("[expenses] URL не задан — пропуск.")
        return
    sys.path.insert(0, str(_REPO_ROOT / "expenses"))
    try:
        from infrastructure.models import ExpenseRequestModel  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "expenses"))
        except ValueError:
            pass

    engine = create_async_engine(make_async_url(url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        n = await s.execute(select(ExpenseRequestModel))
        print(f"[expenses] Заявок expense_requests: {len(list(n.scalars().all()))}")
        if dry_run:
            return
        await s.execute(delete(ExpenseRequestModel))
        await s.commit()
        print("[expenses] Заявки удалены (справочники expense_types / departments и т.д. сохранены).")


async def _wipe_vacation(url: Optional[str], *, dry_run: bool) -> None:
    if not url:
        print("[vacation] URL не задан — пропуск.")
        return
    sys.path.insert(0, str(_REPO_ROOT / "vacation"))
    try:
        from infrastructure.models import AbsenceDay, ScheduleEmployee  # noqa: PLC0415
    finally:
        try:
            sys.path.remove(str(_REPO_ROOT / "vacation"))
        except ValueError:
            pass

    engine = create_async_engine(make_async_url(url), echo=False)
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fac() as s:
        ne = await s.execute(select(AbsenceDay))
        ns = await s.execute(select(ScheduleEmployee))
        print(
            f"[vacation] Сотрудников расписания: {len(list(ns.scalars().all()))}, "
            f"дней отсутствий: {len(list(ne.scalars().all()))}"
        )
        if dry_run:
            return
        await s.execute(delete(AbsenceDay))
        await s.execute(delete(ScheduleEmployee))
        await s.commit()
        print("[vacation] Очищено.")


async def _run(
    *,
    dry_run: bool,
    auth_url: str,
    tt_url: str,
    delete_all_auth_without_tt: bool,
) -> int:
    await _wipe_time_tracking(tt_url, dry_run=dry_run)
    keep = await _tt_non_mock_auth_ids(tt_url)

    await _wipe_auth_users(
        auth_url,
        keep,
        dry_run=dry_run,
        delete_all_without_tt=delete_all_auth_without_tt,
    )

    tickets_url = _env("TICKETS_DATABASE_URL")
    todos_url = _env("TODOS_DATABASE_URL")
    notif_url = _env("NOTIFICATIONS_DATABASE_URL")
    inv_url = _env("INVENTORY_DATABASE_URL")
    att_url = _env("ATTENDANCE_DATABASE_URL")
    exp_url = _env("EXPENSES_DATABASE_URL")
    vac_url = _env("VACATION_DATABASE_URL")

    await _wipe_tickets(tickets_url or None, dry_run=dry_run)
    await _wipe_todos(todos_url or None, dry_run=dry_run)
    await _wipe_notifications(notif_url or None, dry_run=dry_run)
    await _wipe_inventory(inv_url or None, dry_run=dry_run)
    await _wipe_attendance(att_url or None, dry_run=dry_run)
    await _wipe_expenses(exp_url or None, dry_run=dry_run)
    await _wipe_vacation(vac_url or None, dry_run=dry_run)

    if dry_run:
        print("\n[dry-run] Без изменений. Для выполнения: --execute --confirm", repr(CONFIRM_PHRASE))
    else:
        print("\nГотово: данные сервисов по заданным URL очищены; в auth остались только пользователи с TT.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Очистить прикладные БД; оставить в auth только пользователей с профилем TT (не мок)."
    )
    p.add_argument(
        "--auth-database-url",
        type=str,
        default="",
        help="Или переменная AUTH_DATABASE_URL",
    )
    p.add_argument(
        "--time-tracking-database-url",
        type=str,
        default="",
        help="Или TIME_TRACKING_DATABASE_URL, либо DATABASE_URL в контейнере TT",
    )
    p.add_argument(
        "--delete-all-auth-without-tt",
        action="store_true",
        help="Если в TT не осталось ни одного пользователя, всё равно удалить всех из auth.users.",
    )
    p.add_argument(
        "--confirm",
        type=str,
        default="",
        help=f"При --execute передайте дословно {CONFIRM_PHRASE!r}",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")

    args = p.parse_args()
    auth_url = (args.auth_database_url or "").strip() or _env("AUTH_DATABASE_URL")
    tt_url = (
        (args.time_tracking_database_url or "").strip()
        or _env("TIME_TRACKING_DATABASE_URL")
        or _env("DATABASE_URL")
    )

    if not auth_url or not tt_url:
        print(
            "Задайте AUTH_DATABASE_URL и URL БД TT: TIME_TRACKING_DATABASE_URL или DATABASE_URL "
            "(или флаги --auth-database-url / --time-tracking-database-url).",
            file=sys.stderr,
        )
        return 1

    if args.execute and args.confirm.strip() != CONFIRM_PHRASE:
        print(f"Для --execute укажите --confirm {CONFIRM_PHRASE}", file=sys.stderr)
        return 1

    dry = not args.execute
    return asyncio.run(
        _run(
            dry_run=dry,
            auth_url=auth_url,
            tt_url=tt_url,
            delete_all_auth_without_tt=bool(args.delete_all_auth_without_tt),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
