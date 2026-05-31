"""Импорт клиентов, проектов и записей времени из отчёта Harvest (.csv / .xlsx).

Запуск на сервере **без Docker**:

  cd /path/to/tickets-back
  export TIME_TRACKING_DATABASE_URL="postgresql://user:pass@host:5432/kosta_time_tracking"
  pip install -r time_tracking/requirements.txt

  python time_tracking/scripts/import_harvest_time_report.py --dry-run
  python time_tracking/scripts/import_harvest_time_report.py --execute

По умолчанию ищется CSV (точный экспорт Harvest):
  harvest_time_report_from2023-01-23to2026-05-26.csv
Затем xlsx с тем же именем.

URL БД: --database-url или env TIME_TRACKING_DATABASE_URL / DATABASE_URL.

Пользователи сопоставляются с time_tracking_users (включая архив), затем auth DB.
Если сотрудника нет нигде — создаётся архивный TT-пользователь Harvest (все записи импортируются).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import load_workbook

TT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TT_ROOT.parent
HARVEST_REPORT_BASENAME = "harvest_time_report_from2023-01-23to2026-05-26"
HARVEST_CSV_NAME = f"{HARVEST_REPORT_BASENAME}.csv"
HARVEST_XLSX_NAME = f"{HARVEST_REPORT_BASENAME}.xlsx"
DEFAULT_HARVEST_FILE = TT_ROOT / HARVEST_CSV_NAME
HARVEST_IMPORT_EMAIL_DOMAIN = "import.kostalegal.local"
HARVEST_IMPORT_AUTH_ID_FLOOR = 2_000_000_000
HARVEST_IMPORT_PROJECT_BILLABLE = Decimal("0.01")
HARVEST_HOURS_QUANT = Decimal("0.01")
HARVEST_EXTRA_REQUIRED_TASKS = ("My mehnat registration",)


def _harvest_file_candidates(preferred: Path) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []

    def add(p: Path) -> None:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)

    add(preferred)
    for name in (HARVEST_CSV_NAME, HARVEST_XLSX_NAME):
        add(TT_ROOT / name)
        add(Path("/tmp") / name)
        add(REPO_ROOT / "timetrackinck" / name)
        add(Path.cwd() / name)
        add(Path.cwd() / "timetrackinck" / name)
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


def _billable_default_for_harvest_task(task_key: str, project_rows: list[HarvestRow]) -> bool:
    """Как в Harvest: задача non-billable только если все часы по ней non-billable."""
    task_rows = [r for r in project_rows if _norm(r.task_name) == task_key]
    if not task_rows:
        return True
    bill_h = sum((r.hours for r in task_rows if r.is_billable), Decimal("0"))
    non_h = sum((r.hours for r in task_rows if not r.is_billable), Decimal("0"))
    if bill_h <= 0 and non_h > 0:
        return False
    return True


def _expected_task_hours_map(project_rows: list[HarvestRow]) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    acc: dict[str, list[Decimal]] = defaultdict(
        lambda: [Decimal("0"), Decimal("0"), Decimal("0")]
    )
    for r in project_rows:
        key = _norm(r.task_name)
        acc[key][0] += r.hours
        if r.is_billable:
            acc[key][1] += r.hours
        else:
            acc[key][2] += r.hours
    for name in HARVEST_EXTRA_REQUIRED_TASKS:
        acc.setdefault(_norm(name), [Decimal("0"), Decimal("0"), Decimal("0")])
    return {k: (v[0], v[1], v[2]) for k, v in acc.items()}


def _parse_currency(raw: object) -> str:
    text = str(raw or "").strip().upper()
    for code in ("EUR", "USD", "UZS", "GBP", "RUB"):
        if code in text:
            return code
    return "EUR"


def _parse_billable(raw: object) -> bool:
    s = str(raw or "").strip().lower()
    if s in ("no", "false", "0", "n", "non-billable", "non billable", "nonbillable"):
        return False
    if s in ("yes", "true", "1", "y", "billable"):
        return True
    return False


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


def _quantize_harvest_hours(h: Decimal) -> Decimal:
    return h.quantize(HARVEST_HOURS_QUANT, rounding=ROUND_HALF_UP)


def _parse_hours(raw: object) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        h = Decimal(str(raw))
    except Exception:
        return None
    if h <= 0:
        return None
    return _quantize_harvest_hours(h)


def _harvest_seconds_for_hours(hours: Decimal) -> int:
    return int((_quantize_harvest_hours(hours) * Decimal(3600)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class HarvestRow:
    source_row_number: int
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

    def import_ref(self, file_name: str) -> str:
        return f"harvest-import:{file_name}:{self.source_row_number}"


def _csv_field(row: dict[str, str], name: str) -> str:
    if name in row:
        return str(row[name] or "").strip()
    target = name.strip().lower()
    for key, val in row.items():
        if key and key.strip().lower() == target:
            return str(val or "").strip()
    return ""


def _optional_text(raw: str) -> str | None:
    text = (raw or "").strip()
    return text or None


def _row_from_harvest_fields(
    *,
    source_row_number: int,
    work_date_raw: object,
    client_name: str,
    project_name: str,
    project_code_raw: object,
    task_name_raw: object,
    notes_raw: object,
    hours_raw: object,
    billable_raw: object,
    first_name: str,
    last_name: str,
    employee_id_raw: object,
    currency_raw: object,
    external_url_raw: object,
) -> HarvestRow | None:
    work_date = _parse_work_date(work_date_raw)
    client_name = (client_name or "").strip()
    project_name = (project_name or "").strip()
    if not work_date or not client_name or not project_name:
        return None
    hours = _parse_hours(hours_raw)
    if hours is None:
        return None
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    if not first_name and not last_name:
        return None
    return HarvestRow(
        source_row_number=source_row_number,
        work_date=work_date,
        client_name=client_name,
        project_name=project_name,
        project_code=_optional_text(str(project_code_raw or "")),
        task_name=(str(task_name_raw or "Other research").strip() or "Other research"),
        notes=_optional_text(str(notes_raw or "")),
        hours=hours,
        is_billable=_parse_billable(billable_raw),
        first_name=first_name,
        last_name=last_name,
        employee_id=_optional_text(str(employee_id_raw or "")),
        currency=_parse_currency(currency_raw),
        external_reference_url=_optional_text(str(external_url_raw or "")),
    )


def _load_rows_from_csv(path: Path) -> list[HarvestRow]:
    out: list[HarvestRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):
            if not row or not any(str(v or "").strip() for v in row.values()):
                continue
            parsed = _row_from_harvest_fields(
                source_row_number=line_no,
                work_date_raw=_csv_field(row, "Date"),
                client_name=_csv_field(row, "Client"),
                project_name=_csv_field(row, "Project"),
                project_code_raw=_csv_field(row, "Project Code"),
                task_name_raw=_csv_field(row, "Task"),
                notes_raw=_csv_field(row, "Notes"),
                hours_raw=_csv_field(row, "Hours"),
                billable_raw=_csv_field(row, "Billable?"),
                first_name=_csv_field(row, "First Name"),
                last_name=_csv_field(row, "Last Name"),
                employee_id_raw=_csv_field(row, "Employee Id"),
                currency_raw=_csv_field(row, "Currency"),
                external_url_raw=_csv_field(row, "External Reference URL"),
            )
            if parsed is not None:
                out.append(parsed)
    return out


def _load_rows_from_xlsx(path: Path) -> list[HarvestRow]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["Harvest"] if "Harvest" in wb.sheetnames else wb[wb.sheetnames[0]]
        out: list[HarvestRow] = []
        for line_no, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or all(c is None or str(c).strip() == "" for c in row):
                continue
            parsed = _row_from_harvest_fields(
                source_row_number=line_no,
                work_date_raw=row[0],
                client_name=str(row[1] or ""),
                project_name=str(row[2] or ""),
                project_code_raw=row[3],
                task_name_raw=row[4],
                notes_raw=row[5],
                hours_raw=row[6],
                billable_raw=row[7],
                first_name=str(row[10] or ""),
                last_name=str(row[11] or ""),
                employee_id_raw=row[12] if len(row) > 12 else None,
                currency_raw=row[19] if len(row) > 19 else None,
                external_url_raw=row[20] if len(row) > 20 else None,
            )
            if parsed is not None:
                out.append(parsed)
        return out
    finally:
        wb.close()


def _load_rows(path: Path) -> list[HarvestRow]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_rows_from_csv(path)
    if suffix in (".xlsx", ".xlsm"):
        return _load_rows_from_xlsx(path)
    raise SystemExit(f"Неподдерживаемый формат файла: {path.suffix} (нужен .csv или .xlsx)")


def _build_name_index(
    *,
    display_name: str | None,
    email: str | None,
    auth_user_id: int,
    index: dict[str, int],
) -> None:
    if display_name:
        index[_norm(display_name)] = auth_user_id
    if email:
        local = email.split("@", 1)[0].replace(".", " ")
        index[_norm(local)] = auth_user_id


def _match_auth_user_id(row: HarvestRow, index: dict[str, int]) -> int | None:
    key = row.harvest_user_key
    if key in index:
        return index[key]
    rev = _norm(f"{row.last_name} {row.first_name}")
    if rev in index:
        return index[rev]
    return None


def _harvest_display_name(row: HarvestRow) -> str:
    return " ".join(p for p in (row.first_name.strip(), row.last_name.strip()) if p)


def _harvest_import_email(row: HarvestRow) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", row.harvest_user_key).strip(".") or "unknown"
    if row.employee_id:
        emp = re.sub(r"[^a-z0-9]+", "", str(row.employee_id).lower())
        if emp:
            slug = f"{slug}.{emp}"
    local = f"harvest.{slug}"
    max_local = 255 - len(HARVEST_IMPORT_EMAIL_DOMAIN) - 1
    if len(local) > max_local:
        local = local[:max_local]
    return f"{local}@{HARVEST_IMPORT_EMAIL_DOMAIN}"


async def _load_auth_users_for_import(auth_db_url: str) -> tuple[dict[str, int], dict[int, dict]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from infrastructure.database import make_async_url

    engine = create_async_engine(make_async_url(auth_db_url), echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    index: dict[str, int] = {}
    by_id: dict[int, dict] = {}
    try:
        async with session_factory() as s:
            r = await s.execute(
                text(
                    """
                    SELECT id, email, display_name, picture, position, is_blocked, is_archived, time_tracking_role
                    FROM users
                    WHERE email IS NOT NULL AND trim(email) <> ''
                    ORDER BY id
                    """
                )
            )
            for row in r.mappings().all():
                auth_user_id = int(row["id"])
                email = str(row["email"]).strip()
                by_id[auth_user_id] = {
                    "auth_user_id": auth_user_id,
                    "email": email,
                    "display_name": row["display_name"],
                    "picture": row["picture"],
                    "position": row["position"],
                    "is_blocked": bool(row["is_blocked"]),
                    "is_archived": bool(row["is_archived"]),
                    "role": str(row["time_tracking_role"] or "").strip(),
                }
                _build_name_index(
                    display_name=str(row["display_name"]) if row["display_name"] is not None else None,
                    email=email,
                    auth_user_id=auth_user_id,
                    index=index,
                )
    finally:
        await engine.dispose()
    return index, by_id


async def _run(*, path: Path, execute: bool, database_url: str, auth_db_url: str = "", replace: bool = False) -> int:
    from sqlalchemy import and_, case, delete, func, select, update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from application.client_expense_category_defaults import seed_default_expense_categories_for_client
    from application.project_billable_rate_sync import project_uses_shared_billable
    from application.project_partner_requirement import (
        ensure_projects_have_partner_assignee,
        job_title_indicates_partner,
    )
    from infrastructure.repository_shared import _now_utc
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
    from infrastructure.repositories import TimeTrackingUserRepository

    rows = _load_rows(path)
    if not rows:
        print("Файл пуст или не содержит строк данных.")
        return 1

    print(f"Файл: {path}")
    print(f"Формат: {path.suffix.lower()} (1 строка файла = 1 запись времени)")
    harvest_source_name = path.name
    print(f"Строк в отчёте: {len(rows)}")
    print(f"Клиентов: {len({r.client_name for r in rows})}")
    print(f"Проектов: {len({(r.client_name, r.project_name) for r in rows})}")
    print(f"Пользователей Harvest: {len({r.harvest_user_key for r in rows})}")
    expected_hours_total = sum((r.hours for r in rows), Decimal("0"))
    expected_billable_total = sum((r.hours for r in rows if r.is_billable), Decimal("0"))
    expected_non_billable_total = expected_hours_total - expected_billable_total
    print(
        f"Часы в файле Harvest: всего {expected_hours_total}, "
        f"billable {expected_billable_total}, non-billable {expected_non_billable_total}"
    )

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
            _build_name_index(
                display_name=u.display_name,
                email=u.email,
                auth_user_id=int(u.auth_user_id),
                index=index,
            )
        return index

    match_auth_user_id = _match_auth_user_id

    async def find_tt_user_by_email(session: AsyncSession, email: str) -> TimeTrackingUserModel | None:
        r = await session.execute(
            select(TimeTrackingUserModel).where(TimeTrackingUserModel.email == email)
        )
        return r.scalars().one_or_none()

    async def next_harvest_auth_user_id(session: AsyncSession) -> int:
        from sqlalchemy import func

        r = await session.execute(select(func.max(TimeTrackingUserModel.auth_user_id)))
        mx = int(r.scalar_one_or_none() or 0)
        return max(HARVEST_IMPORT_AUTH_ID_FLOOR, mx + 1)

    def register_user_in_index(
        row: HarvestRow,
        *,
        auth_user_id: int,
        display_name: str,
        email: str,
        user_index: dict[str, int],
    ) -> None:
        user_index[row.harvest_user_key] = auth_user_id
        _build_name_index(
            display_name=display_name,
            email=email,
            auth_user_id=auth_user_id,
            index=user_index,
        )

    async def ensure_tt_user_from_auth(session: AsyncSession, auth_user: dict) -> bool:
        tur = TimeTrackingUserRepository(session)
        if await tur.get_by_auth_user_id(auth_user["auth_user_id"]):
            return False
        pos = (str(auth_user.get("position") or "").strip()) or "Harvest import"
        await tur.upsert_user(
            auth_user_id=auth_user["auth_user_id"],
            email=auth_user["email"],
            display_name=auth_user.get("display_name"),
            picture=auth_user.get("picture"),
            role=str(auth_user.get("role") or ""),
            is_blocked=bool(auth_user.get("is_blocked", False)),
            is_archived=bool(auth_user.get("is_archived", True)),
            position=pos,
            update_position=True,
        )
        await session.flush()
        return True

    async def ensure_harvest_placeholder_user(
        session: AsyncSession,
        row: HarvestRow,
        *,
        user_index: dict[str, int],
        tt_by_auth: dict[int, TimeTrackingUserModel],
        harvest_placeholder_ids: dict[str, int],
        dry_run_placeholder_next: list[int],
    ) -> tuple[int, bool]:
        email = _harvest_import_email(row)
        display_name = _harvest_display_name(row)

        existing = await find_tt_user_by_email(session, email)
        if existing is not None:
            uid = int(existing.auth_user_id)
            register_user_in_index(
                row,
                auth_user_id=uid,
                display_name=display_name,
                email=email,
                user_index=user_index,
            )
            tt_by_auth[uid] = existing
            harvest_placeholder_ids[row.harvest_user_key] = uid
            return uid, False

        cached = harvest_placeholder_ids.get(row.harvest_user_key)
        if cached is not None:
            return cached, False

        if not execute:
            dry_run_placeholder_next[0] -= 1
            uid = dry_run_placeholder_next[0]
            harvest_placeholder_ids[row.harvest_user_key] = uid
            register_user_in_index(
                row,
                auth_user_id=uid,
                display_name=display_name,
                email=email,
                user_index=user_index,
            )
            return uid, False

        tur = TimeTrackingUserRepository(session)
        uid = await next_harvest_auth_user_id(session)
        await tur.upsert_user(
            auth_user_id=uid,
            email=email,
            display_name=display_name,
            role="",
            is_blocked=False,
            is_archived=True,
            position="Harvest import",
            update_position=True,
        )
        await session.flush()
        refreshed = await tur.get_by_auth_user_id(uid)
        if refreshed is not None:
            tt_by_auth[uid] = refreshed
        harvest_placeholder_ids[row.harvest_user_key] = uid
        register_user_in_index(
            row,
            auth_user_id=uid,
            display_name=display_name,
            email=email,
            user_index=user_index,
        )
        return uid, True

    async def resolve_auth_user_id(
        session: AsyncSession,
        row: HarvestRow,
        user_index: dict[str, int],
        auth_index: dict[str, int],
        auth_by_id: dict[int, dict],
        tt_by_auth: dict[int, TimeTrackingUserModel],
        harvest_placeholder_ids: dict[str, int],
        dry_run_placeholder_next: list[int],
    ) -> tuple[int, bool, str]:
        """Всегда возвращает auth_user_id. Третье значение: tt|auth|harvest."""
        uid = match_auth_user_id(row, user_index)
        if uid is not None:
            return uid, False, "tt"

        if auth_index:
            uid = match_auth_user_id(row, auth_index)
            if uid is not None:
                auth_user = auth_by_id.get(uid)
                if auth_user is not None:
                    created = False
                    if execute:
                        created = await ensure_tt_user_from_auth(session, auth_user)
                        register_user_in_index(
                            row,
                            auth_user_id=uid,
                            display_name=str(auth_user.get("display_name") or _harvest_display_name(row)),
                            email=auth_user["email"],
                            user_index=user_index,
                        )
                        refreshed = await TimeTrackingUserRepository(session).get_by_auth_user_id(uid)
                        if refreshed is not None:
                            tt_by_auth[uid] = refreshed
                    return uid, created, "auth"

        uid, created = await ensure_harvest_placeholder_user(
            session,
            row,
            user_index=user_index,
            tt_by_auth=tt_by_auth,
            harvest_placeholder_ids=harvest_placeholder_ids,
            dry_run_placeholder_next=dry_run_placeholder_next,
        )
        return uid, created, "harvest"

    async def find_first_partner_auth_user_id(session: AsyncSession) -> int | None:
        r = await session.execute(
            select(TimeTrackingUserModel.auth_user_id, TimeTrackingUserModel.position).order_by(
                TimeTrackingUserModel.is_archived.asc(),
                TimeTrackingUserModel.auth_user_id.asc(),
            )
        )
        for auth_user_id, position in r.all():
            if job_title_indicates_partner(position):
                return int(auth_user_id)
        return None

    async def finalize_imported_project(
        session: AsyncSession,
        *,
        client_id: str,
        project_id: str,
        granter: int | None,
        access_repo: UserProjectAccessRepository,
        project_repo: ClientProjectRepository,
        stats: Counter,
    ) -> None:
        row = await project_repo.get_by_id(client_id, project_id)
        if row is None:
            return
        if not project_uses_shared_billable(row):
            await project_repo.update(
                client_id,
                project_id,
                {
                    "billable_rate_type": "per_project",
                    "project_billable_rate_amount": HARVEST_IMPORT_PROJECT_BILLABLE,
                },
            )
            stats["project_billable_configured"] += 1
        try:
            await ensure_projects_have_partner_assignee(
                session,
                access_repo,
                {project_id},
                projects=project_repo,
            )
        except ValueError:
            partner_uid = await find_first_partner_auth_user_id(session)
            if partner_uid is None:
                print(
                    f"  ВНИМАНИЕ: проект «{row.name}» — нет партнёра в команде. "
                    "Добавьте партнёра вручную, иначе редактирование состава может не сохраниться."
                )
                return
            if granter is None:
                granter = partner_uid
            await access_repo.grant_access_if_absent(
                partner_uid,
                project_id,
                granted_by_auth_user_id=granter,
                projects=project_repo,
            )
            stats["project_partner_added"] += 1

    def expected_hours_for_project(client_name: str, project_name: str) -> Decimal:
        total = Decimal("0")
        ckey = _norm(client_name)
        pkey = _norm(project_name)
        for r in rows:
            if _norm(r.client_name) == ckey and _norm(r.project_name) == pkey:
                total += r.hours
        return total

    def expected_hours_breakdown_for_project(
        client_name: str, project_name: str
    ) -> tuple[Decimal, Decimal, Decimal]:
        total = Decimal("0")
        billable = Decimal("0")
        ckey = _norm(client_name)
        pkey = _norm(project_name)
        for r in rows:
            if _norm(r.client_name) == ckey and _norm(r.project_name) == pkey:
                total += r.hours
                if r.is_billable:
                    billable += r.hours
        return total, billable, total - billable

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
        out: dict[str, str] = {}
        for name, tid in r.all():
            key = _norm(name)
            if key not in out:
                out[key] = str(tid)
        return out

    async def dedupe_project_tasks_by_name(
        session: AsyncSession,
        project_id: str,
        task_repo: ClientTaskRepository,
    ) -> int:
        r = await session.execute(
            select(TimeManagerClientTaskModel)
            .where(TimeManagerClientTaskModel.project_id == project_id)
            .order_by(TimeManagerClientTaskModel.created_at.asc())
        )
        groups: dict[str, list[TimeManagerClientTaskModel]] = defaultdict(list)
        for task in r.scalars().all():
            groups[_norm(task.name)].append(task)
        merged = 0
        for group in groups.values():
            if len(group) <= 1:
                continue
            keeper = group[0]
            for dup in group[1:]:
                await session.execute(
                    update(TimeEntryModel)
                    .where(TimeEntryModel.task_id == dup.id)
                    .values(task_id=keeper.id)
                )
                await task_repo.delete(project_id, dup.id)
                merged += 1
        return merged

    def rows_for_project(client_name: str, project_name: str) -> list[HarvestRow]:
        ckey = _norm(client_name)
        pkey = _norm(project_name)
        return [r for r in rows if _norm(r.client_name) == ckey and _norm(r.project_name) == pkey]

    async def ensure_harvest_project_tasks(
        session: AsyncSession,
        project_id: str,
        project_rows: list[HarvestRow],
        task_repo: ClientTaskRepository,
    ) -> dict[str, str]:
        if not execute:
            tmap: dict[str, str] = {}
            for r in project_rows:
                tmap[_norm(r.task_name)] = r.task_name
            for name in HARVEST_EXTRA_REQUIRED_TASKS:
                tmap.setdefault(_norm(name), name)
            return tmap

        merged = await dedupe_project_tasks_by_name(session, project_id, task_repo)
        if merged:
            print(f"  Объединены дубликаты задач проекта: {merged}")
        tmap = await task_map_for_project(session, project_id)

        display_names: dict[str, str] = {}
        for r in project_rows:
            display_names[_norm(r.task_name)] = r.task_name.strip()
        for name in HARVEST_EXTRA_REQUIRED_TASKS:
            display_names.setdefault(_norm(name), name)

        for key, display_name in sorted(display_names.items(), key=lambda x: x[1].lower()):
            billable_default = _billable_default_for_harvest_task(key, project_rows)
            task_id = tmap.get(key)
            if task_id:
                await task_repo.update(
                    project_id,
                    task_id,
                    {"billable_by_default": billable_default},
                )
            else:
                row_task = await task_repo.create(
                    project_id=project_id,
                    name=display_name,
                    default_billable_rate=Decimal("0"),
                    billable_by_default=billable_default,
                )
                task_id = row_task.id
                tmap[key] = task_id
                stats["task_created"] += 1
        await session.flush()
        return tmap

    async def db_task_breakdown_for_project(
        session: AsyncSession,
        project_id: str,
    ) -> dict[str, tuple[str, Decimal, Decimal, Decimal, bool]]:
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
            key = _norm(row.name)
            if key in out:
                prev = out[key]
                out[key] = (
                    prev[0],
                    prev[1] + _quantize_harvest_hours(Decimal(str(row.total))),
                    prev[2] + _quantize_harvest_hours(Decimal(str(row.billable))),
                    prev[3] + _quantize_harvest_hours(Decimal(str(row.non_billable))),
                    bool(row.billable_by_default),
                )
            else:
                out[key] = (
                    str(row.name),
                    _quantize_harvest_hours(Decimal(str(row.total))),
                    _quantize_harvest_hours(Decimal(str(row.billable))),
                    _quantize_harvest_hours(Decimal(str(row.non_billable))),
                    bool(row.billable_by_default),
                )
        return out

    async def entry_exists_by_import_ref(session: AsyncSession, import_ref: str) -> bool:
        r = await session.execute(
            select(TimeEntryModel.id).where(
                TimeEntryModel.external_reference_url == import_ref,
                TimeEntryModel.voided_at.is_(None),
            ).limit(1)
        )
        return r.scalar_one_or_none() is not None

    auth_index: dict[str, int] = {}
    auth_by_id: dict[int, dict] = {}
    if auth_db_url.strip():
        print(f"Auth DB: сопоставление уволенных сотрудников по display_name")
        auth_index, auth_by_id = await _load_auth_users_for_import(auth_db_url.strip())

    try:
        async with session_factory() as session:
            tt_users = list((await session.execute(select(TimeTrackingUserModel))).scalars().all())
            user_index = build_user_index(tt_users)
            tt_by_auth = {int(u.auth_user_id): u for u in tt_users}
            harvest_placeholder_ids: dict[str, int] = {}
            dry_run_placeholder_next = [0]
            harvest_only_names: set[str] = set()

            stats = Counter()
            client_repo = ClientRepository(session)
            project_repo = ClientProjectRepository(session)
            task_repo = ClientTaskRepository(session)
            entry_repo = TimeEntryRepository(session)
            access_repo = UserProjectAccessRepository(session)

            if execute and replace:
                pairs = sorted({(r.client_name, r.project_name) for r in rows})
                for client_name, project_name in pairs:
                    existing_client = await find_client_by_name(session, client_name)
                    if existing_client is None:
                        continue
                    existing_project = await find_project(session, existing_client.id, project_name)
                    if existing_project is None:
                        continue
                    result = await session.execute(
                        delete(TimeEntryModel).where(TimeEntryModel.project_id == existing_project.id)
                    )
                    deleted = int(result.rowcount or 0)
                    if deleted:
                        print(
                            f"Удалены старые записи: {client_name} / {project_name} — {deleted} шт."
                        )
                await session.flush()

            client_cache: dict[str, str] = {}
            project_cache: dict[tuple[str, str], str] = {}
            project_meta: dict[str, tuple[str, str, str]] = {}
            task_cache: dict[str, dict[str, str]] = {}
            projects_tasks_initialized: set[str] = set()
            client_currency: dict[str, str] = {}
            granted_access: set[tuple[int, str]] = set()
            for hr in rows:
                client_currency.setdefault(_norm(hr.client_name), hr.currency)

            # Сначала гарантируем TT-пользователя для каждого имени из Harvest (даже без регистрации в auth).
            unique_by_harvest_user: dict[str, HarvestRow] = {}
            for hr in rows:
                unique_by_harvest_user.setdefault(hr.harvest_user_key, hr)
            print(f"\nПользователи Harvest (уникальных): {len(unique_by_harvest_user)}")
            for _key, sample in sorted(unique_by_harvest_user.items(), key=lambda x: x[0]):
                uid, tt_created, user_source = await resolve_auth_user_id(
                    session,
                    sample,
                    user_index,
                    auth_index,
                    auth_by_id,
                    tt_by_auth,
                    harvest_placeholder_ids,
                    dry_run_placeholder_next,
                )
                if user_source == "harvest":
                    harvest_only_names.add(sample.harvest_user_key)
                    if tt_created and execute:
                        stats["tt_user_harvest_placeholder"] += 1
                        print(f"  + создан TT-пользователь Harvest: {_harvest_display_name(sample)}")
                elif user_source == "auth" and tt_created:
                    stats["tt_user_created"] += 1

            granter: int | None = None

            for hr in rows:
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
                        await session.flush()
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
                        project_meta[existing_p.id] = (client_id, hr.client_name, hr.project_name)
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
                            billable_rate_type="per_project",
                            project_billable_rate_amount=HARVEST_IMPORT_PROJECT_BILLABLE,
                        )
                        project_cache[pkey] = created_p.id
                        project_meta[created_p.id] = (client_id, hr.client_name, hr.project_name)
                        stats["project_created"] += 1
                        await session.flush()
                    else:
                        project_cache[pkey] = f"<new:{hr.project_name}>"
                        stats["project_created"] += 1

                project_id = project_cache[pkey]
                if project_id.startswith("<new:"):
                    stats["entry_planned"] += 1
                    continue

                if project_id not in projects_tasks_initialized:
                    proj_rows = rows_for_project(hr.client_name, hr.project_name)
                    task_cache[project_id] = await ensure_harvest_project_tasks(
                        session,
                        project_id,
                        proj_rows,
                        task_repo,
                    )
                    projects_tasks_initialized.add(project_id)
                    if execute:
                        extra = ", ".join(HARVEST_EXTRA_REQUIRED_TASKS)
                        print(f"  Задачи проекта «{hr.project_name}»: из CSV + обязательные: {extra}")

                tmap = task_cache[project_id]
                tname = _norm(hr.task_name)
                task_id = tmap.get(tname)
                if not task_id:
                    stats["entry_error"] += 1
                    print(f"  ОШИБКА: нет задачи «{hr.task_name}» в проекте {hr.project_name}")
                    continue

                auth_user_id, _tt_created, user_source = await resolve_auth_user_id(
                    session,
                    hr,
                    user_index,
                    auth_index,
                    auth_by_id,
                    tt_by_auth,
                    harvest_placeholder_ids,
                    dry_run_placeholder_next,
                )
                if user_source == "harvest":
                    harvest_only_names.add(hr.harvest_user_key)
                if granter is None:
                    u = tt_by_auth.get(auth_user_id)
                    if u is None or not u.is_archived:
                        granter = auth_user_id
                if auth_user_id not in tt_by_auth and execute:
                    refreshed = await TimeTrackingUserRepository(session).get_by_auth_user_id(auth_user_id)
                    if refreshed is not None:
                        tt_by_auth[auth_user_id] = refreshed

                description = hr.notes
                if execute:
                    if granter is None:
                        granter = auth_user_id
                    access_key = (auth_user_id, project_id)
                    if access_key not in granted_access:
                        await access_repo.grant_access_if_absent(
                            auth_user_id,
                            project_id,
                            granted_by_auth_user_id=granter,
                            projects=project_repo,
                        )
                        granted_access.add(access_key)
                        stats["project_access_granted"] += 1
                    import_ref = hr.import_ref(harvest_source_name)
                    if await entry_exists_by_import_ref(session, import_ref):
                        stats["entry_duplicate"] += 1
                        continue
                    sec = _harvest_seconds_for_hours(hr.hours)
                    try:
                        entry_id = str(uuid.uuid4())
                        file_hours = hr.hours
                        session.add(
                            TimeEntryModel(
                                id=entry_id,
                                auth_user_id=auth_user_id,
                                work_date=hr.work_date,
                                duration_seconds=sec,
                                hours=file_hours,
                                rounded_hours=file_hours,
                                is_billable=hr.is_billable,
                                project_id=project_id,
                                task_id=task_id,
                                description=description,
                                external_reference_url=import_ref,
                                created_at=_now_utc(),
                                updated_at=None,
                            )
                        )
                        stats["entry_created"] += 1
                    except ValueError as e:
                        stats["entry_error"] += 1
                        print(
                            f"  ОШИБКА записи {hr.work_date} {_harvest_display_name(hr)} "
                            f"({hr.hours} ч): {e}"
                        )
                else:
                    stats["entry_planned"] += 1

            if harvest_only_names:
                print("\nСозданы/использованы TT-пользователи Harvest (нет в auth/TT):")
                for name in sorted(harvest_only_names):
                    print(f"  - {name}")

            if execute:
                for project_id, (client_id, _client_name, _project_name) in project_meta.items():
                    await finalize_imported_project(
                        session,
                        client_id=client_id,
                        project_id=project_id,
                        granter=granter,
                        access_repo=access_repo,
                        project_repo=project_repo,
                        stats=stats,
                    )

                await session.flush()

                print(
                    f"\nЗаписей времени: создано {stats['entry_created']}, "
                    f"дубликаты {stats['entry_duplicate']}, ошибок {stats['entry_error']}"
                )

                print("\nСверка часов по проектам (Billable? из колонки 8 файла):")
                db_hours_total = Decimal("0")
                db_billable_total = Decimal("0")
                db_non_billable_total = Decimal("0")
                hours_ok = True
                for project_id, (client_id, client_name, project_name) in project_meta.items():
                    db_total, db_billable, db_non_billable = await entry_repo.aggregate_totals_for_project(
                        project_id
                    )
                    db_total = _quantize_harvest_hours(Decimal(str(db_total)))
                    db_billable = _quantize_harvest_hours(Decimal(str(db_billable)))
                    db_non_billable = _quantize_harvest_hours(Decimal(str(db_non_billable)))
                    exp_total, exp_billable, exp_non_billable = expected_hours_breakdown_for_project(
                        client_name, project_name
                    )
                    db_hours_total += db_total
                    db_billable_total += db_billable
                    db_non_billable_total += db_non_billable
                    project_ok = (
                        db_total == exp_total
                        and db_billable == exp_billable
                        and db_non_billable == exp_non_billable
                    )
                    if not project_ok:
                        hours_ok = False
                    status = "OK" if project_ok else "РАСХОЖДЕНИЕ"
                    print(
                        f"  [{status}] {project_name}: "
                        f"файл {exp_total} (billable {exp_billable}, non-billable {exp_non_billable}), "
                        f"БД {db_total} (billable {db_billable}, non-billable {db_non_billable})"
                    )

                print("\nСверка часов по задачам (как в Harvest):")
                tasks_ok = True
                for project_id, (_client_id, client_name, project_name) in project_meta.items():
                    proj_rows = rows_for_project(client_name, project_name)
                    expected_tasks = _expected_task_hours_map(proj_rows)
                    db_tasks = await db_task_breakdown_for_project(session, project_id)
                    print(f"  Проект «{project_name}»:")
                    for key in sorted(
                        expected_tasks,
                        key=lambda k: (db_tasks.get(k, (k,))[0] if k in db_tasks else k).lower(),
                    ):
                        exp_total, exp_bill, exp_non = expected_tasks[key]
                        exp_total = _quantize_harvest_hours(exp_total)
                        exp_bill = _quantize_harvest_hours(exp_bill)
                        exp_non = _quantize_harvest_hours(exp_non)
                        exp_billable_flag = _billable_default_for_harvest_task(key, proj_rows)
                        label = db_tasks[key][0] if key in db_tasks else key
                        if key not in db_tasks:
                            tasks_ok = False
                            print(f"    [ОТСУТСТВУЕТ] {label}: файл {exp_total} ч")
                            continue
                        name, db_total, db_bill, db_non, db_flag = db_tasks[key]
                        task_ok = (
                            db_total == exp_total
                            and db_bill == exp_bill
                            and db_non == exp_non
                            and db_flag == exp_billable_flag
                        )
                        if not task_ok:
                            tasks_ok = False
                        status = "OK" if task_ok else "РАСХОЖДЕНИЕ"
                        billable_label = "billable" if exp_billable_flag else "non-billable"
                        print(
                            f"    [{status}] {name} ({billable_label}): "
                            f"файл {exp_total} (b {exp_bill}, nb {exp_non}), "
                            f"БД {db_total} (b {db_bill}, nb {db_non}), "
                            f"флаг задачи={'billable' if db_flag else 'non-billable'}"
                        )

                print(
                    f"\nИтого: файл {expected_hours_total} "
                    f"(billable {expected_billable_total}, non-billable {expected_non_billable_total}), "
                    f"БД {db_hours_total} (billable {db_billable_total}, non-billable {db_non_billable_total})"
                )
                if not hours_ok or db_hours_total != expected_hours_total:
                    print("ОШИБКА: сумма часов в БД не совпадает с файлом Harvest.")
                    await session.rollback()
                    return 1

                if not tasks_ok:
                    print("ОШИБКА: часы или billable-флаг задач не совпадают с Harvest.")
                    await session.rollback()
                    return 1

                if stats["entry_error"] > 0:
                    print(f"\nОШИБКА: не импортировано записей: {stats['entry_error']}")
                    await session.rollback()
                    return 1

                processed = stats["entry_created"] + stats["entry_duplicate"]
                if processed != len(rows):
                    print(
                        f"\nОШИБКА: в файле {len(rows)} строк, в БД создано/найдено {processed} "
                        f"(создано: {stats['entry_created']}, дубликаты: {stats['entry_duplicate']})."
                    )
                    await session.rollback()
                    return 1

                await session.commit()
                print("\nИмпорт выполнен.")
            else:
                print("\nDry-run (без записи в БД). Добавьте --execute для импорта.")

            print(
                f"Клиентов создано: {stats['client_created']}, уже было: {stats['client_exists']}; "
                f"проектов создано: {stats['project_created']}, уже было: {stats['project_exists']}; "
                f"задач создано: {stats['task_created']}; "
                f"доступ к проекту выдан: {stats['project_access_granted']}; "
                f"партнёров добавлено: {stats['project_partner_added']}; "
                f"TT из auth: {stats['tt_user_created']}, placeholder Harvest: {stats['tt_user_harvest_placeholder']}; "
                f"записей времени: {stats['entry_created'] or stats['entry_planned']} "
                f"(дубликаты: {stats['entry_duplicate']}, ошибок: {stats['entry_error']})."
            )
            expected = len(rows)
            imported = stats["entry_created"] + stats["entry_duplicate"] if execute else stats["entry_planned"]
            if imported + stats["entry_error"] < expected:
                print(
                    f"ВНИМАНИЕ: в отчёте {expected} строк, обработано {imported} "
                    f"(+ ошибок: {stats['entry_error']})."
                )
            elif execute:
                print(f"Все {expected} строк отчёта в БД (создано {stats['entry_created']}, дубликаты {stats['entry_duplicate']}).")
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Импорт Harvest time report (.csv / .xlsx) в time tracking.")
    p.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_HARVEST_FILE,
        help=f"Путь к .csv или .xlsx (по умолчанию: {HARVEST_CSV_NAME}).",
    )
    p.add_argument(
        "--database-url",
        type=str,
        default="",
        help="PostgreSQL URL (иначе TIME_TRACKING_DATABASE_URL или DATABASE_URL).",
    )
    p.add_argument(
        "--auth-db-url",
        type=str,
        default="",
        help="Auth PostgreSQL URL для уволенных (env AUTH_DATABASE_URL).",
    )
    p.add_argument(
        "--replace",
        action="store_true",
        help="Удалить старые записи времени по проектам из файла перед импортом (чистый 1:1).",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Только статистика, без записи.")
    g.add_argument("--execute", action="store_true", help="Записать в БД.")
    args = p.parse_args()

    harvest_file = _resolve_harvest_file(args.file)
    if harvest_file is None:
        print(f"Файл не найден: {args.file}")
        print(f"Ожидаемые имена: {HARVEST_CSV_NAME} или {HARVEST_XLSX_NAME}")
        print("Проверьте пути:")
        for c in _harvest_file_candidates(args.file):
            print(f"  - {c}")
        print(
            "\nКонтейнер time_tracking — скопируйте CSV с хоста:\n"
            f"  docker cp timetrackinck/{HARVEST_CSV_NAME} "
            "$(docker compose ps -q time_tracking):/tmp/" + HARVEST_CSV_NAME
        )
        return 1

    database_url = _resolve_database_url(args.database_url or None)
    auth_db_url = (args.auth_db_url or os.environ.get("AUTH_DATABASE_URL") or "").strip()
    _configure_database_url(database_url)
    return asyncio.run(
        _run(
            path=harvest_file,
            execute=args.execute,
            database_url=database_url,
            auth_db_url=auth_db_url,
            replace=args.replace,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
