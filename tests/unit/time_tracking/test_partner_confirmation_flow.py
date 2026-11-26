"""Partner report confirmation, end to end on a real database: submit → sign → revoke → priority → lists → comments → delete."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

import application.invoice_service  # noqa: F401  loads every mapped model in the right order
import application.partner_report_confirmation_service as svc
from infrastructure.auth_directory_cache import invalidate_partner_confirmation_read_caches
from infrastructure.models import TimeEntryModel, TimeTrackingUserModel, TimeTrackingUserProjectAccessModel
from infrastructure.report_cache import invalidate_all_reports
from support.tt_invoice import TT_NOW as NOW

D_FROM, D_TO = date(2026, 11, 1), date(2026, 11, 30)
LAWYER = {"id": 7, "role": "employee"}
PARTNER_A = {"id": 21, "role": "employee"}
PARTNER_B = {"id": 22, "role": "employee"}
OUTSIDER = {"id": 99, "role": "employee"}
ADMIN = {"id": 1, "role": "Администратор", "time_tracking_role": "manager"}


@pytest.fixture(autouse=True)
def _fresh_caches():
    invalidate_partner_confirmation_read_caches()
    invalidate_all_reports()
    from infrastructure import auth_directory_cache as cache

    if hasattr(cache, "invalidate_partners_by_projects"):
        cache.invalidate_partners_by_projects()
    yield
    invalidate_partner_confirmation_read_caches()


@pytest.fixture()
async def team(tt_db):
    s = tt_db
    for uid, pos in ((7, "Associate"), (21, "Partner"), (22, "Managing Partner"), (99, "Associate")):
        s.add(TimeTrackingUserModel(auth_user_id=uid, email=f"u{uid}@kl.com", position=pos, created_at=NOW))
    await s.flush()
    for uid in (7, 21, 22):
        s.add(TimeTrackingUserProjectAccessModel(id=f"a{uid}", auth_user_id=uid, project_id="p1", created_at=NOW))
    s.add(TimeEntryModel(
        id="e1", auth_user_id=7, work_date=date(2026, 11, 3), duration_seconds=3600, hours=Decimal("1"),
        rounded_hours=Decimal("1"), project_id="p1", is_billable=True, created_at=NOW,
    ))
    await s.commit()
    return s


async def test_title_and_completion_rules(team):
    from infrastructure.repository_clients import ClientProjectRepository

    title = await svc.build_report_confirmation_title(team, ClientProjectRepository(team), "p1", D_FROM, D_TO)
    assert title.startswith("Falcon 2026-11-01")
    assert await svc.build_report_confirmation_title(team, ClientProjectRepository(team), "ghost", D_FROM, D_TO) == "ghost 2026-11-01–2026-11-30"
    assert svc.partners_confirmation_is_complete([1, 2], set()) is False
    assert svc.partners_confirmation_is_complete([], {5}) is True
    assert svc.partners_confirmation_is_complete([1, 2], {1}) is False
    assert svc.partners_confirmation_is_complete([1, 2], {1, 2, 3}) is True
    with pytest.raises(HTTPException):
        svc._viewer_id({})


async def test_full_flow(team):
    with pytest.raises(HTTPException):
        await svc.submit_partner_report_confirmation_from_preview(team, LAWYER, project_id=" ", date_from=D_FROM, date_to=D_TO, authorization=None)
    with pytest.raises(HTTPException):
        await svc.submit_partner_report_confirmation_from_preview(team, LAWYER, project_id="p1", date_from=D_TO, date_to=D_FROM, authorization=None)

    out = await svc.submit_partner_report_confirmation_from_preview(team, LAWYER, project_id="p1", date_from=D_FROM, date_to=D_TO, authorization=None)
    rid = out["id"]
    assert out["status"] == "pending_partners"
    again = await svc.submit_partner_report_confirmation_from_preview(team, LAWYER, project_id="p1", date_from=D_FROM, date_to=D_TO, authorization=None)
    assert again["id"] == rid

    with pytest.raises(HTTPException) as err:
        await svc.confirm_partner_report_confirmation(team, LAWYER, rid, authorization=None)
    assert err.value.status_code == 403
    with pytest.raises(HTTPException):
        await svc.confirm_partner_report_confirmation(team, PARTNER_A, "missing", authorization=None)

    pending = await svc.list_pending_partner_confirmations(team, PARTNER_A, authorization=None, scope="mine")
    assert pending["total"] == 1 and pending["signaturePendingCount"] == 1
    badge = await svc.count_partner_pending_signatures_for_viewer(team, PARTNER_A, authorization=None)
    assert badge["count"] == 1
    with pytest.raises(HTTPException):
        await svc.list_pending_partner_confirmations(team, LAWYER, authorization=None, scope="all")
    everything = await svc.list_pending_partner_confirmations(team, ADMIN, authorization=None, scope="all", priority="yellow")
    assert everything["priorityCounts"]["all"] >= 1

    signed = await svc.confirm_partner_report_confirmation(team, PARTNER_A, rid, authorization=None)
    assert signed["status"] == "pending_partners"
    with pytest.raises(HTTPException):
        await svc.confirm_partner_report_confirmation(team, PARTNER_A, rid, authorization=None)

    with pytest.raises(HTTPException) as err:
        await svc.set_partner_confirmation_review_priority(team, OUTSIDER, rid, "red", authorization=None)
    assert err.value.status_code == 403
    red = await svc.set_partner_confirmation_review_priority(team, LAWYER, rid, "red", authorization=None)
    assert red["reviewPriority"] == "red"

    done = await svc.confirm_partner_report_confirmation(team, PARTNER_B, rid, authorization=None)
    assert done["status"] == "fully_confirmed"
    await svc.ensure_fully_confirmed_partner_period_or_403(team, project_id="p1", date_from=D_FROM, date_to=D_TO)

    confirmed = await svc.list_confirmed_partner_confirmations(team, ADMIN, authorization=None)
    assert [row["id"] for row in confirmed] == [rid]
    mine = await svc.list_confirmed_partner_confirmations(team, PARTNER_A, authorization=None, date_from=D_FROM, date_to=D_TO)
    assert [row["id"] for row in mine] == [rid]

    with pytest.raises(HTTPException):
        await svc.create_partner_confirmation_comment(team, OUTSIDER, rid, text="hi", authorization=None)
    with pytest.raises(HTTPException):
        await svc.create_partner_confirmation_comment(team, LAWYER, rid, text="   ", authorization=None)
    with pytest.raises(HTTPException):
        await svc.create_partner_confirmation_comment(team, LAWYER, rid, text="x" * 4001, authorization=None)
    comment = await svc.create_partner_confirmation_comment(team, LAWYER, rid, text="Проверьте часы", authorization=None)
    with pytest.raises(HTTPException) as err:
        await svc.update_partner_confirmation_comment(team, PARTNER_A, rid, comment["id"], text="чужой", authorization=None)
    assert err.value.status_code == 403
    edited = await svc.update_partner_confirmation_comment(team, LAWYER, rid, comment["id"], text="Исправлено", authorization=None)
    assert edited["text"] == "Исправлено"
    with pytest.raises(HTTPException):
        await svc.update_partner_confirmation_comment(team, LAWYER, rid, "nope", text="x", authorization=None)
    comments = await svc.list_partner_confirmation_comments(team, PARTNER_B, rid, authorization=None)
    assert len(comments) == 1
    with pytest.raises(HTTPException):
        await svc.list_partner_confirmation_comments(team, OUTSIDER, rid, authorization=None)

    with pytest.raises(HTTPException):
        await svc.revoke_partner_report_confirmation_signature(team, OUTSIDER, rid, 21, authorization=None)
    with pytest.raises(HTTPException):
        await svc.revoke_partner_report_confirmation_signature(team, LAWYER, rid, 0, authorization=None)
    with pytest.raises(HTTPException):
        await svc.revoke_partner_report_confirmation_signature(team, LAWYER, rid, 99, authorization=None)
    back = await svc.revoke_partner_report_confirmation_signature(team, PARTNER_B, rid, 22, authorization=None)
    assert back["status"] == "pending_partners"

    history = await svc.list_partner_confirmation_history(team, LAWYER, rid, authorization=None)
    assert history

    with pytest.raises(HTTPException):
        await svc.delete_partner_report_confirmation(team, OUTSIDER, rid)
    assert (await svc.delete_partner_report_confirmation(team, LAWYER, rid))["ok"] is True
    with pytest.raises(HTTPException):
        await svc.delete_partner_report_confirmation(team, LAWYER, rid)


async def test_submit_requires_partners_and_project_rows(team):
    from infrastructure.repository_reports import ReportSnapshotRepository

    snap = await ReportSnapshotRepository(team).create(
        name="s", report_type="time", group_by="projects", filters={}, created_by_user_id=7,
        rows_data=[{"source_type": "project", "source_id": "p1", "data": {"projectId": "p1"}}],
    )
    await team.commit()
    with pytest.raises(HTTPException):
        await svc.submit_partner_report_confirmation(team, OUTSIDER, snapshot_id=snap.id, project_id="p1", date_from=D_FROM, date_to=D_TO, authorization=None)
    with pytest.raises(HTTPException):
        await svc.submit_partner_report_confirmation(team, LAWYER, snapshot_id=snap.id, project_id="p9", date_from=D_FROM, date_to=D_TO, authorization=None)
    await team.execute(TimeTrackingUserProjectAccessModel.__table__.delete().where(TimeTrackingUserProjectAccessModel.auth_user_id.in_([21, 22])))
    await team.commit()
    with pytest.raises(HTTPException, match="партнёры"):
        await svc.submit_partner_report_confirmation(team, LAWYER, snapshot_id=snap.id, project_id="p1", date_from=D_FROM, date_to=D_TO, authorization=None)


async def test_invalidation_after_entry_change(team):
    out = await svc.submit_partner_report_confirmation_from_preview(team, LAWYER, project_id="p1", date_from=D_FROM, date_to=D_TO, authorization=None)
    assert await svc.invalidate_confirmations_after_time_entry_change(team, project_id=None, work_date=date(2026, 11, 3)) == 0
    n = await svc.invalidate_confirmations_after_time_entry_change(team, project_id="p1", work_date=date(2026, 11, 3))
    await team.commit()
    assert n >= 1
    await svc.invalidate_confirmations_after_row_edit(team, "missing", "row")
    assert out["id"]
