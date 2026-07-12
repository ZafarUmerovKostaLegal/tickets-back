from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import ScheduleEmployee


HIDDEN_EMAILS = frozenset({"admin@local"})
HIDDEN_LOCAL_PARTS = frozenset({"admin", "info"})
HIDDEN_DISPLAY_NAMES = frozenset({"главный администратор"})


def normalize_full_name(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().replace("ё", "е").split())


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def is_hidden_staff_user(user: dict) -> bool:
    email = normalize_email(user.get("email"))
    if email in HIDDEN_EMAILS:
        return True
    if email and "@" in email:
        local = email.split("@", 1)[0]
        if local in HIDDEN_LOCAL_PARTS:
            return True
    display = normalize_full_name(user.get("display_name") or user.get("displayName"))
    return display in HIDDEN_DISPLAY_NAMES


def staff_display_name(user: dict) -> str:
    name = (user.get("display_name") or user.get("displayName") or "").strip()
    if name:
        return name[:500]
    email = (user.get("email") or "").strip()
    if email:
        return email[:500]
    uid = int(user.get("id") or 0)
    return f"User #{uid}"[:500]


@dataclass(frozen=True)
class SyncScheduleEmployeesResult:
    year: int
    created: int
    linked_orphans: int
    updated: int
    skipped_archived: int
    skipped_hidden: int


@dataclass(frozen=True)
class ScheduleEmployeesAuditResult:
    """Read-only identity health for a schedule year — never mutates."""

    year: int
    totalRows: int
    linkedToAuth: int
    unlinkedOrphans: int
    duplicateEmailsAmongOrphans: int
    duplicateNamesAmongOrphans: int
    note: str


async def audit_schedule_employees_for_year(
    session: AsyncSession,
    *,
    year: int,
) -> ScheduleEmployeesAuditResult:
    r = await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.year == year))
    rows = list(r.scalars().all())
    linked = 0
    orphans: list[ScheduleEmployee] = []
    for row in rows:
        if row.auth_user_id is not None:
            linked += 1
        else:
            orphans.append(row)

    email_counts: dict[str, int] = {}
    name_counts: dict[str, int] = {}
    for row in orphans:
        ek = normalize_email(row.email)
        if ek:
            email_counts[ek] = email_counts.get(ek, 0) + 1
        nk = normalize_full_name(row.full_name)
        if nk:
            name_counts[nk] = name_counts.get(nk, 0) + 1

    dup_emails = sum(1 for n in email_counts.values() if n > 1)
    dup_names = sum(1 for n in name_counts.values() if n > 1)

    return ScheduleEmployeesAuditResult(
        year=year,
        totalRows=len(rows),
        linkedToAuth=linked,
        unlinkedOrphans=len(orphans),
        duplicateEmailsAmongOrphans=dup_emails,
        duplicateNamesAmongOrphans=dup_names,
        note=(
            "Read-only. Prefer linking by auth_user_id; name/email matching is for Excel orphans only. "
            "Does not auto-unlink or wipe rows."
        ),
    )


async def sync_schedule_employees_for_year(
    session: AsyncSession,
    *,
    year: int,
    staff_users: list[dict],
) -> SyncScheduleEmployeesResult:
    """Привязать всех активных auth-пользователей к строкам графика на год.

    - Существующая строка с тем же ``auth_user_id`` — обновляем ФИО/email.
    - «Осиротевшая» строка (импорт Excel без auth_user_id) — привязываем по
      email или нормализованному ФИО.
    - Иначе создаём новую строку с ``auth_user_id``.
    """
    r = await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.year == year))
    existing = list(r.scalars().all())

    by_auth_id: dict[int, ScheduleEmployee] = {}
    orphan_by_email: dict[str, ScheduleEmployee] = {}
    orphan_by_name: dict[str, ScheduleEmployee] = {}
    for row in existing:
        if row.auth_user_id is not None:
            by_auth_id[int(row.auth_user_id)] = row
            continue
        email_key = normalize_email(row.email)
        if email_key and email_key not in orphan_by_email:
            orphan_by_email[email_key] = row
        name_key = normalize_full_name(row.full_name)
        if name_key and name_key not in orphan_by_name:
            orphan_by_name[name_key] = row

    created = 0
    linked_orphans = 0
    updated = 0
    skipped_archived = 0
    skipped_hidden = 0

    for raw in staff_users:
        if is_hidden_staff_user(raw):
            skipped_hidden += 1
            continue
        if raw.get("is_archived") or raw.get("is_blocked"):
            skipped_archived += 1
            continue
        uid = int(raw["id"])
        label = staff_display_name(raw)
        email = normalize_email(raw.get("email")) or None
        email_store = email[:320] if email else None

        row = by_auth_id.get(uid)
        if row is not None:
            changed = False
            if row.full_name != label:
                row.full_name = label
                changed = True
            if email_store and row.email != email_store:
                row.email = email_store
                changed = True
            if changed:
                updated += 1
            continue

        orphan: ScheduleEmployee | None = None
        if email:
            orphan = orphan_by_email.pop(email, None)
        if orphan is None:
            orphan = orphan_by_name.pop(normalize_full_name(label), None)

        if orphan is not None:
            orphan.auth_user_id = uid
            orphan.full_name = label
            if email_store:
                orphan.email = email_store
            by_auth_id[uid] = orphan
            linked_orphans += 1
            continue

        row = ScheduleEmployee(
            year=year,
            excel_row_no=None,
            auth_user_id=uid,
            full_name=label,
            email=email_store,
            planned_period_note=None,
        )
        session.add(row)
        by_auth_id[uid] = row
        created += 1

    await session.flush()
    return SyncScheduleEmployeesResult(
        year=year,
        created=created,
        linked_orphans=linked_orphans,
        updated=updated,
        skipped_archived=skipped_archived,
        skipped_hidden=skipped_hidden,
    )
