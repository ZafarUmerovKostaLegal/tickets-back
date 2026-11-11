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
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _parse_csv_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _find_header_row(lines: list[str]) -> int:
    for i, line in enumerate(lines[:30]):
        low = line.lower()
        if "first name" in low and "date" in low and "time" in low:
            return i
    return -1


def parse_transaction_csv_rows(
    text: str,
    name_map: Mapping[str, tuple[str, str]],
    *,
    camera_ip: str,
) -> tuple[list[dict[str, Any]], CsvImportResult]:
    result = CsvImportResult(camera_ip=camera_ip)
    result.name_map = {k: v[0] for k, v in name_map.items()}

    # Strip BOM / Hikvision title rows before the real header.
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header_idx = _find_header_row(raw_lines)
    if header_idx < 0:
        raise ValueError("CSV header not found (expected First Name,Date,Week,Time)")
    body = "\n".join(raw_lines[header_idx:])
    reader = csv.DictReader(io.StringIO(body))
    if not reader.fieldnames:
        raise ValueError("CSV has no columns")

    def col(*names: str) -> str | None:
        fields = list(reader.fieldnames or [])
        lowered = { (f or "").strip().lower(): f for f in fields }
        for n in names:
            if n.lower() in lowered:
                return lowered[n.lower()]
        return None

    name_col = col("First Name", "Name")
    date_col = col("Date")
    time_col = col("Time")
    if not name_col or not date_col or not time_col:
        raise ValueError("CSV must include First Name, Date, Time columns")

    by_key: dict[tuple[str, str, datetime, str], dict[str, Any]] = {}
    date_min: date | None = None
    date_max: date | None = None
    now = datetime.utcnow()

    for row in reader:
        result.rows_read += 1
        csv_name = _norm_name(str(row.get(name_col) or ""))
        if not csv_name:
            continue
        day = _parse_csv_date(str(row.get(date_col) or ""))
        tm = _parse_csv_time(str(row.get(time_col) or ""))
        if day is None or tm is None:
            continue
        mapped = name_map.get(csv_name) or name_map.get(csv_name.split(" ")[0])
        if not mapped:
            result.rows_unmapped += 1
            result.unmapped_names[csv_name] = result.unmapped_names.get(csv_name, 0) + 1
            continue
        emp, display = mapped
        event_time = datetime.combine(day, tm, tzinfo=_OFFICE_TZ)
        key = (camera_ip, emp, event_time, _CSV_CHECKPOINT)
        by_key[key] = {
            "camera_ip": camera_ip,
            "person_id": emp,
            "event_time": event_time,
            "name": display[:256],
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
