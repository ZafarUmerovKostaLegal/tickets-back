"""Import Hikvision Transaction CSV (First Name,Date,Week,Time) into camera_events."""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from infrastructure.camera_events_repo import ensure_camera_events_indexes, upsert_camera_events
from infrastructure.config import get_settings
from infrastructure.database import async_session_factory
from infrastructure.hikvision_client import get_users_from_devices
from infrastructure.hikvision_hosts import configured_hikvision_hosts

logger = logging.getLogger(__name__)

_OFFICE_TZ = timezone(timedelta(hours=5))
_CSV_CHECKPOINT = "Door"
_CSV_LABEL = "csv-import"

# Transaction export only has First Name — disambiguate colliding given names.
_NAME_OVERRIDES: dict[str, str] = {
    "Shoira": "00000036",  # Shoira Mirrakhimova (active card; short "Shoira" is stale)
    "Iroda": "00000025",  # Iroda Tokhirova
    "Sayora": "00000054",  # Sayora Shamuratova
}


@dataclass
class CsvImportResult:
    path: str = ""
    rows_read: int = 0
    rows_mapped: int = 0
    rows_unmapped: int = 0
    upserted: int = 0
    camera_ip: str = ""
    name_map: dict[str, str] = field(default_factory=dict)
    unmapped_names: dict[str, int] = field(default_factory=dict)
    date_min: str | None = None
    date_max: str | None = None


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _first_token(value: str) -> str:
    parts = _norm_name(value).split(" ")
    return parts[0] if parts else ""


def build_csv_name_map(hikvision_users: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    """Map CSV First Name → (employee_no, display_name)."""
    by_exact: dict[str, list[tuple[str, str]]] = {}
    by_first: dict[str, list[tuple[str, str]]] = {}
    for u in hikvision_users:
        emp = str(u.get("employee_no") or "").strip()
        name = _norm_name(str(u.get("name") or ""))
        if not emp or not name:
            continue
        by_exact.setdefault(name.casefold(), []).append((emp, name))
        by_first.setdefault(_first_token(name).casefold(), []).append((emp, name))

    result: dict[str, tuple[str, str]] = {}

    # Prefer exact short-name identities (name == first token).
    for key, options in by_exact.items():
        for emp, name in options:
            if name.casefold() == _first_token(name).casefold():
                result[_first_token(name)] = (emp, name)

    for first_cf, options in by_first.items():
        first_display = options[0][1].split(" ")[0]
        if first_display in result:
            continue
        # Unique first token → that person.
        unique_emps = {emp for emp, _ in options}
        if len(unique_emps) == 1:
            emp, name = options[0]
            result[first_display] = (emp, name)
            continue
        # Prefer person whose full name equals the first token only.
        exact_only = [(emp, name) for emp, name in options if name.casefold() == first_cf]
        if len(exact_only) == 1:
            result[first_display] = exact_only[0]
            continue

    # Full names as written on the terminal (unique only) — exports with a full "Name" column,
    # so "Iroda Bekmurodova" is not swallowed by the "Iroda" first-name override.
    for options in by_exact.values():
        if len({emp for emp, _ in options}) == 1:
            emp, name = options[0]
            result.setdefault(name, (emp, name))

    # Apply manual overrides last.
    emp_to_name = {
        str(u.get("employee_no") or "").strip(): _norm_name(str(u.get("name") or ""))
        for u in hikvision_users
        if str(u.get("employee_no") or "").strip()
    }
    for csv_name, emp in _NAME_OVERRIDES.items():
        display = emp_to_name.get(emp) or csv_name
        result[csv_name] = (emp, display)

    # AlinaA is a distinct hikvision short name.
    if "alinaa" in by_exact:
        emp, name = by_exact["alinaa"][0]
        result["AlinaA"] = (emp, name)

    return result


def _parse_csv_time(raw: str) -> time | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y.%m.%d")


def _parse_csv_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    head = s.split(" ")[0].split("T")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    return None


def _parse_csv_datetime(raw: str) -> tuple[date, time] | None:
    """"2026-03-02 09:01:12", "02.03.2026 09:01", "2026-03-02T09:01:12+05:00"."""
    s = (raw or "").strip()
    if not s:
        return None
    if "T" in s and s[:4].isdigit():
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(_OFFICE_TZ)
            return dt.date(), dt.time().replace(microsecond=0)
        except ValueError:
            pass
    parts = s.split(None, 1)
    if len(parts) != 2:
        return None
    day = _parse_csv_date(parts[0])
    tm = _parse_csv_time(parts[1])
    if day is None or tm is None:
        return None
    return day, tm


_ID_HEADERS = ("person id", "employee id", "employee no.", "employee no", "id", "табельный номер")
_NAME_HEADERS = ("first name", "name", "person name", "employee name", "full name", "имя", "фио", "сотрудник")
_TIME_HEADERS = ("time", "event time", "access time", "authentication time", "date time", "datetime", "время", "дата и время")


def _header_cells(line: str) -> list[str]:
    return [c.strip().strip('"').lower() for c in re.split(r"[,;\t]", line)]


def _find_header_row(lines: list[str]) -> int:
    for i, line in enumerate(lines[:30]):
        cells = _header_cells(line)
        has_who = any(c in _NAME_HEADERS or c in _ID_HEADERS for c in cells)
        has_time = any(c in _TIME_HEADERS for c in cells)
        if has_who and has_time:
            return i
    return -1


def _sniff_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def _id_variants(raw: str) -> list[str]:
    s = (raw or "").strip()
    if not s:
        return []
    out = [s]
    if s.isdigit():
        stripped = s.lstrip("0") or "0"
        out.extend([stripped, stripped.zfill(8)])
    return out


def parse_transaction_csv_rows(
    text: str,
    name_map: Mapping[str, tuple[str, str]],
    *,
    camera_ip: str,
) -> tuple[list[dict[str, Any]], CsvImportResult]:
    """Hikvision / HCWebControl transaction export → camera_events rows.

    Accepts the classic "First Name,Date,Week,Time" layout and richer ones: Person ID /
    Employee No., a full Name (or First + Last Name), one combined date-time column,
    comma / semicolon / tab delimiters and DD.MM.YYYY dates.
    """
    result = CsvImportResult(camera_ip=camera_ip)
    result.name_map = {k: v[0] for k, v in name_map.items()}

    # Strip BOM / Hikvision title rows before the real header.
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header_idx = _find_header_row(raw_lines)
    if header_idx < 0:
        raise ValueError("CSV header not found (expected a name / person ID column and a time column)")
    delimiter = _sniff_delimiter(raw_lines[header_idx])
    body = "\n".join(raw_lines[header_idx:])
    reader = csv.DictReader(io.StringIO(body), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("CSV has no columns")

    lowered = {(f or "").strip().strip('"').lower(): f for f in (reader.fieldnames or [])}

    def col(*names: str) -> str | None:
        for n in names:
            if n in lowered:
                return lowered[n]
        return None

    id_col = col(*_ID_HEADERS)
    first_col = col("first name", "имя")
    last_col = col("last name", "фамилия")
    name_col = first_col or col("name", "person name", "employee name", "full name", "фио", "сотрудник")
    date_col = col("date", "дата")
    time_col = col("time", "время")
    datetime_col = col("event time", "access time", "authentication time", "date time", "datetime", "дата и время")
    if not name_col and not id_col:
        raise ValueError("CSV must include a name or person ID column")
    if not datetime_col and not time_col:
        raise ValueError("CSV must include a time column")

    # Case-insensitive lookups: full names first, then the first token.
    by_name_cf = {k.casefold(): v for k, v in name_map.items()}
    by_emp: dict[str, tuple[str, str]] = {}
    for emp, display in name_map.values():
        for key in _id_variants(emp):
            by_emp.setdefault(key, (emp, display))

    by_key: dict[tuple[str, str, datetime, str], dict[str, Any]] = {}
    date_min: date | None = None
    date_max: date | None = None
    now = datetime.utcnow()

    for row in reader:
        result.rows_read += 1
        first = _norm_name(str(row.get(name_col) or "")) if name_col else ""
        last = _norm_name(str(row.get(last_col) or "")) if last_col else ""
        csv_name = _norm_name(f"{first} {last}") if last else first
        raw_id = str(row.get(id_col) or "").strip() if id_col else ""
        if not csv_name and not raw_id:
            continue

        day: date | None = None
        tm: time | None = None
        if datetime_col:
            parsed = _parse_csv_datetime(str(row.get(datetime_col) or ""))
            if parsed:
                day, tm = parsed
        if day is None and time_col:
            raw_time = str(row.get(time_col) or "")
            parsed = _parse_csv_datetime(raw_time)  # "Time" sometimes holds date + time
            if parsed:
                day, tm = parsed
            elif date_col:
                day = _parse_csv_date(str(row.get(date_col) or ""))
                tm = _parse_csv_time(raw_time)
        if day is None or tm is None:
            continue

        mapped: tuple[str, str] | None = None
        if raw_id:
            for key in _id_variants(raw_id):
                if key in by_emp:
                    mapped = by_emp[key]
                    break
            if mapped is None:
                mapped = (raw_id, csv_name or f"#{raw_id}")
        if mapped is None and csv_name:
            mapped = by_name_cf.get(csv_name.casefold()) or by_name_cf.get(_first_token(csv_name).casefold())
        if not mapped:
            label = csv_name or raw_id
            result.rows_unmapped += 1
            result.unmapped_names[label] = result.unmapped_names.get(label, 0) + 1
            continue
        emp, display = mapped
        event_time = datetime.combine(day, tm, tzinfo=_OFFICE_TZ)
        key = (camera_ip, emp, event_time, _CSV_CHECKPOINT)
        by_key[key] = {
            "camera_ip": camera_ip,
            "person_id": emp,
            "event_time": event_time,
            "name": (display or csv_name)[:256],
            "department": None,
            "checkpoint": _CSV_CHECKPOINT,
            "attendance_status": None,
            "door_no": None,
            "label": _CSV_LABEL,
            "ingested_at": now,
        }
        result.rows_mapped += 1
        date_min = day if date_min is None or day < date_min else date_min
        date_max = day if date_max is None or day > date_max else date_max

    result.date_min = date_min.isoformat() if date_min else None
    result.date_max = date_max.isoformat() if date_max else None
    return list(by_key.values()), result


async def load_hikvision_users_for_map() -> list[dict[str, Any]]:
    import asyncio

    settings = get_settings()
    hosts = configured_hikvision_hosts(settings)
    if not hosts:
        return []
    devices = await asyncio.to_thread(
        get_users_from_devices,
        hosts=hosts[:1],
        port=settings.hikvision_device_port,
        user=settings.hikvision_device_user,
        password=settings.hikvision_device_password,
        max_users_per_device=500,
        timeout=30,
    )
    users: list[dict[str, Any]] = []
    for dev in devices or []:
        users.extend(dev.get("users") or [])
    return users


async def import_transaction_csv(
    path: str | Path,
    *,
    camera_ip: str | None = None,
    hikvision_users: list[dict[str, Any]] | None = None,
) -> CsvImportResult:
    settings = get_settings()
    hosts = configured_hikvision_hosts(settings)
    ip = (camera_ip or (hosts[0] if hosts else "")).strip() or "192.168.230.48"

    users = hikvision_users
    if users is None:
        users = await load_hikvision_users_for_map()
    name_map = build_csv_name_map(users)

    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    rows, result = parse_transaction_csv_rows(text, name_map, camera_ip=ip)
    result.path = str(path)

    async with async_session_factory() as session:
        await ensure_camera_events_indexes(session)
        result.upserted = await upsert_camera_events(session, rows)

    logger.info(
        "CSV import %s: read=%s mapped=%s unmapped=%s upserted=%s range=%s..%s",
        result.path,
        result.rows_read,
        result.rows_mapped,
        result.rows_unmapped,
        result.upserted,
        result.date_min,
        result.date_max,
    )
    return result


async def import_transaction_csv_bytes(
    data: bytes,
    *,
    filename: str = "upload.csv",
    camera_ip: str | None = None,
) -> CsvImportResult:
    settings = get_settings()
    hosts = configured_hikvision_hosts(settings)
    ip = (camera_ip or (hosts[0] if hosts else "")).strip() or "192.168.230.48"
    users = await load_hikvision_users_for_map()
    name_map = build_csv_name_map(users)
    text = data.decode("utf-8-sig", errors="replace")
    rows, result = parse_transaction_csv_rows(text, name_map, camera_ip=ip)
    result.path = filename
    async with async_session_factory() as session:
        await ensure_camera_events_indexes(session)
        result.upserted = await upsert_camera_events(session, rows)
    return result
