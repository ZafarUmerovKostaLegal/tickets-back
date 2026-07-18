
from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.repositories import ClientProjectRepository, ClientRepository
from presentation.schemas import TimeEntryOut


async def time_entries_to_out(
    session: AsyncSession,
    rows: Sequence[Any],
) -> list[TimeEntryOut]:
    """Serialize time entries and attach project/client labels for UI lists."""
    if not rows:
        return []
    pids = sorted({
        str(getattr(r, "project_id", "") or "").strip()
        for r in rows
        if getattr(r, "project_id", None)
    })
    projects = await ClientProjectRepository(session).list_by_ids_global(pids) if pids else []
    by_pid = {str(p.id): p for p in projects}
    cids = sorted({str(p.client_id).strip() for p in projects if getattr(p, "client_id", None)})
    clients = await ClientRepository(session).get_by_ids(set(cids)) if cids else {}

    out: list[TimeEntryOut] = []
    for r in rows:
        item = TimeEntryOut.model_validate(r)
        pid = (item.project_id or "").strip()
        if not pid:
            out.append(item)
            continue
        proj = by_pid.get(pid)
        if not proj:
            out.append(item)
            continue
        cid = str(proj.client_id).strip() if proj.client_id else None
        client = clients.get(cid) if cid else None
        item.project_name = (proj.name or "").strip() or None
        item.client_id = cid or None
        item.client_name = ((client.name if client else None) or "").strip() or None
        out.append(item)
    return out


async def time_entry_to_out(session: AsyncSession, row: Any) -> TimeEntryOut:
    return (await time_entries_to_out(session, [row]))[0]
