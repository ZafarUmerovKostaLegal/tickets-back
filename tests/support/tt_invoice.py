"""Shared helpers for time-tracking invoice tests (the service path is set by the package conftest)."""

from __future__ import annotations

from datetime import date, datetime, timezone

TT_NOW = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)


async def make_invoice(session, **kw):
    import application.invoice_service as svc

    args = dict(
        actor_auth_user_id=1, client_id="c1", project_id="p1",
        issue_date=date(2026, 11, 26), due_date=date(2026, 12, 10),
        currency=None, tax_percent=None, tax2_percent=None, discount_percent=None,
        client_note=None, internal_note=None,
        lines=[{"lineKind": "manual", "description": "Consulting", "quantity": "2", "unitAmount": "150"}],
        time_entry_ids=None, expense_ids=None,
    )
    args.update(kw)
    inv = await svc.create_invoice(session, **args)
    await session.commit()
    return inv
