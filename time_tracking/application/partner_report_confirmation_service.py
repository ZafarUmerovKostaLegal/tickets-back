
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.project_partner_users import list_partner_auth_user_ids_for_project
from application.partner_confirmation_team_scope import (
    list_report_auth_user_ids_for_project_period,
    list_team_member_auth_user_ids_for_partner,
)
from application.reports.partner_scope import (
    normalize_partner_pending_scope,
    pending_confirmation_visible_for_user_mine,
    viewer_can_view_all_pending_partner_confirmations,
)
from application.report_viewer_scope import (
    list_partner_project_ids_for_viewer,
    viewer_can_see_all_partner_confirmations,
)
from infrastructure.repository_access import UserProjectAccessRepository
from infrastructure.repository_clients import ClientProjectRepository
from infrastructure.repository_partner_report_confirmations import (
    PartnerReportConfirmationRepository,
    project_id_from_snapshot_row,
)
from infrastructure.repository_entries import TimeEntryRepository
from infrastructure.repository_reports import ReportSnapshotRepository


def _org_role(viewer: dict) -> str:
    return (viewer.get("role") or "").strip()


def _viewer_id(viewer: dict) -> int:
    uid = viewer.get("id")
    if uid is None:
        raise HTTPException(status_code=403, detail="В токене нет id пользователя")
    return int(uid)


def _viewer_can_see_all_confirmations(viewer: dict) -> bool:
    return viewer_can_see_all_partner_confirmations(viewer)


async def build_report_confirmation_title(
    session: AsyncSession,
    projects: ClientProjectRepository,
    project_id: str,
    date_from: date,
    date_to: date,
) -> str:
    pr = await projects.get_by_id_global(project_id)
    code = (project_id or "").strip()
    if pr is not None:
        if pr.code and str(pr.code).strip():
            code = str(pr.code).strip()
        elif pr.name and str(pr.name).strip():
            code = str(pr.name).strip()
    return f"{code} {date_from.isoformat()}–{date_to.isoformat()}"


def partners_confirmation_is_complete(
    required_partners: list[int],
    signed_partner_ids: set[int],
) -> bool:
    """True when every current project partner signed, or partners were removed but signatures remain."""
    if not signed_partner_ids:
        return False
    if not required_partners:
        return True
    return set(required_partners).issubset(signed_partner_ids)


async def reconcile_confirmation_if_complete(
    conf_repo: PartnerReportConfirmationRepository,
    request_row,
    required_partners: list[int],
) -> bool:
    if (getattr(request_row, "status", None) or "").strip() == "fully_confirmed":
        return False
    signed_ids = {s.partner_auth_user_id for s in (request_row.signatures or [])}
    if not partners_confirmation_is_complete(required_partners, signed_ids):
        return False
    await conf_repo.mark_fully_confirmed(request_row.id)
    request_row.status = "fully_confirmed"
    return True


async def _reconcile_all_completable_pending(
    session: AsyncSession,
    access_repo: UserProjectAccessRepository,
    *,
    authorization: str | None,
) -> None:
    conf_repo = PartnerReportConfirmationRepository(session)
    candidates = await conf_repo.list_all_pending()
    changed = False
    for m in candidates:
        partners = await list_partner_auth_user_ids_for_project(
            session, access_repo, m.project_id, authorization=authorization
        )
        if await reconcile_confirmation_if_complete(conf_repo, m, partners):
            changed = True
    if changed:
        await session.commit()


def _comment_to_out(c) -> dict:
    return {
        "id": c.id,
        "authUserId": int(c.auth_user_id),
        "text": c.text,
        "createdAt": c.created_at.isoformat(),
    }


async def _entry_counts_for_request_rows(session: AsyncSession, rows: list) -> dict[str, int]:
    if not rows:
        return {}
    repo = TimeEntryRepository(session)
    periods = list(dict.fromkeys((m.project_id, m.date_from, m.date_to) for m in rows))
    counts_by_period = await repo.count_entries_by_project_periods(periods)
    out: dict[str, int] = {}
    for m in rows:
        key = (m.project_id, m.date_from, m.date_to)
        out[m.id] = int(counts_by_period.get(key, 0))
    return out


def _request_to_out(
    m,
    required_partners: list[int],
    *,
    comments_count: int | None = None,
    last_comment=None,
    entry_count: int | None = None,
) -> dict:
    sigs = [
        {
            "partnerAuthUserId": s.partner_auth_user_id,
            "confirmedAt": s.confirmed_at.isoformat(),
        }
        for s in (m.signatures or [])
    ]
    signed_ids = {s.partner_auth_user_id for s in (m.signatures or [])}
    pending_ids = [p for p in required_partners if p not in signed_ids]
    out = {
        "id": m.id,
        "snapshotId": m.snapshot_id,
        "projectId": m.project_id,
        "dateFrom": m.date_from.isoformat(),
        "dateTo": m.date_to.isoformat(),
        "title": m.title,
        "status": m.status,
        "submittedByAuthUserId": m.submitted_by_auth_user_id,
        "requiredPartnerAuthUserIds": list(required_partners),
        "pendingPartnerAuthUserIds": pending_ids,
        "signatures": sigs,
        "createdAt": m.created_at.isoformat(),
        "updatedAt": m.updated_at.isoformat() if m.updated_at else None,
    }
    if comments_count is not None:
        out["commentsCount"] = int(comments_count)
        out["lastComment"] = _comment_to_out(last_comment) if last_comment is not None else None
    if entry_count is not None:
        ec = int(entry_count)
        out["entryCount"] = ec
        out["isEmpty"] = ec == 0
    return out


def can_bypass_partner_confirmation_gate(viewer: dict) -> bool:
    """Для расширений (например аварийный обход владельцами); по умолчанию не используется."""
    return _viewer_can_see_all_confirmations(viewer)


async def ensure_fully_confirmed_partner_period_or_403(
    session: AsyncSession,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
) -> None:
    repo = PartnerReportConfirmationRepository(session)
    if not await repo.has_fully_confirmed_for_project_period(project_id, date_from, date_to):
        raise HTTPException(
            status_code=403,
            detail="Нет полного подтверждения партнёров, охватывающего указанный период по этому проекту "
            "(интервал должен полностью входить в даты подтверждённого отчёта). "
            "Сначала отправьте отчёт на подтверждение и дождитесь подписей всех партнёров проекта.",
        )


async def submit_partner_report_confirmation(
    session: AsyncSession,
    viewer: dict,
    *,
    snapshot_id: str,
    project_id: str,
    date_from: date,
    date_to: date,
    authorization: str | None,
) -> dict:
    vid = _viewer_id(viewer)
    snap_repo = ReportSnapshotRepository(session)
    snap = await snap_repo.get_by_id(snapshot_id, load_rows=False)
    if not snap or snap.created_by_user_id != vid:
        raise HTTPException(status_code=404, detail="Снимок отчёта не найден")
    conf_repo = PartnerReportConfirmationRepository(session)
    if not await conf_repo.snapshot_has_project_row(snapshot_id, project_id):
        raise HTTPException(
            status_code=400,
            detail="В снимке нет строк по указанному проекту",
        )
    access_repo = UserProjectAccessRepository(session)
    partners = await list_partner_auth_user_ids_for_project(
        session, access_repo, project_id, authorization=authorization
    )
    if not partners:
        raise HTTPException(
            status_code=400,
            detail="По проекту не найдены партнёры для подтверждения (доступ и роль/должность)",
        )
    projects = ClientProjectRepository(session)
    title = await build_report_confirmation_title(session, projects, project_id, date_from, date_to)
    row = await conf_repo.upsert_submit(
        snapshot_id=snapshot_id,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        title=title,
        submitted_by_auth_user_id=vid,
    )
    await session.commit()
    loaded = await conf_repo.get_request_by_id(row.id, load_signatures=True)
    if not loaded:
        raise HTTPException(status_code=500, detail="internal")
    return _request_to_out(loaded, partners)


async def submit_partner_report_confirmation_from_preview(
    session: AsyncSession,
    viewer: dict,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
    authorization: str | None,
) -> dict:
    """Создаёт минимальный снимок отчёта по проекту и сразу вызывает submit (для предпросмотра без отдельного POST /snapshots)."""
    pid = (project_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="projectId required")
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="dateTo не может быть раньше dateFrom")
    conf_repo = PartnerReportConfirmationRepository(session)
    existing = await conf_repo.find_latest_pending_for_project_period(pid, date_from, date_to)
    if existing:
        access_repo = UserProjectAccessRepository(session)
        partners = await list_partner_auth_user_ids_for_project(
            session, access_repo, pid, authorization=authorization
        )
        if not partners:
            raise HTTPException(
                status_code=400,
                detail="По проекту не найдены партнёры для подтверждения (доступ и роль/должность)",
            )
        if await reconcile_confirmation_if_complete(conf_repo, existing, partners):
            await session.commit()
        return _request_to_out(existing, partners)
    vid = _viewer_id(viewer)
    projects = ClientProjectRepository(session)
    snap_name = await build_report_confirmation_title(session, projects, pid, date_from, date_to)
    snap_repo = ReportSnapshotRepository(session)
    snap = await snap_repo.create(
        name=snap_name,
        report_type="time",
        group_by="projects",
        filters={
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "projectIds": [pid],
        },
        created_by_user_id=vid,
        rows_data=[
            {
                "source_type": "project",
                "source_id": pid,
                "data": {"projectId": pid},
            }
        ],
    )
    await session.flush()
    return await submit_partner_report_confirmation(
        session,
        viewer,
        snapshot_id=snap.id,
        project_id=pid,
        date_from=date_from,
        date_to=date_to,
        authorization=authorization,
    )


async def confirm_partner_report_confirmation(
    session: AsyncSession,
    viewer: dict,
    request_id: str,
    *,
    authorization: str | None,
) -> dict:
    vid = _viewer_id(viewer)
    conf_repo = PartnerReportConfirmationRepository(session)
    req = await conf_repo.get_request_by_id(request_id, load_signatures=True)
    if not req:
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")
    access_repo = UserProjectAccessRepository(session)
    partners = await list_partner_auth_user_ids_for_project(
        session, access_repo, req.project_id, authorization=authorization
    )
    if not partners:
        raise HTTPException(status_code=400, detail="По проекту не найдены партнёры")
    signed_ids = set(await conf_repo.list_signature_partner_ids(request_id))
    pending_ids = [p for p in partners if p not in signed_ids]
    if vid not in pending_ids:
        raise HTTPException(
            status_code=403,
            detail="Подтверждать могут только партнёры, ожидающие подписи по этой заявке",
        )
    if not await conf_repo.partner_has_signed(request_id, vid):
        await conf_repo.add_signature(request_id, vid)
        await session.flush()
    signed_ids = set(await conf_repo.list_signature_partner_ids(request_id))
    if partners_confirmation_is_complete(partners, signed_ids):
        await conf_repo.mark_fully_confirmed(request_id)
    await session.commit()
    req = await conf_repo.get_request_by_id(request_id, load_signatures=True)
    if not req:
        raise HTTPException(status_code=500, detail="internal")
    partners = await list_partner_auth_user_ids_for_project(
        session, access_repo, req.project_id, authorization=authorization
    )
    return _request_to_out(req, partners)


async def delete_partner_report_confirmation(
    session: AsyncSession,
    viewer: dict,
    request_id: str,
) -> dict:
    """Удаляет заявку на подтверждение (на проверке или полностью подтверждённую).

    Разрешено отправителю заявки или пользователям с полным доступом к спискам отчётов.
    Записи времени не удаляются.
    """
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    vid = _viewer_id(viewer)
    conf_repo = PartnerReportConfirmationRepository(session)
    req = await conf_repo.get_request_by_id(rid, load_signatures=True)
    if not req:
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")
    is_submitter = int(req.submitted_by_auth_user_id) == vid
    can_manage = (
        viewer_can_view_all_pending_partner_confirmations(viewer)
        or _viewer_can_see_all_confirmations(viewer)
    )
    if not is_submitter and not can_manage:
        raise HTTPException(
            status_code=403,
            detail="Удалить может только отправитель отчёта или администратор",
        )
    ok = await conf_repo.delete_request(rid)
    if not ok:
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")
    await session.commit()
    return {"ok": True, "id": rid}


async def revoke_partner_report_confirmation_signature(
    session: AsyncSession,
    viewer: dict,
    request_id: str,
    partner_auth_user_id: int,
    *,
    authorization: str | None,
) -> dict:
    """Снимает одну подпись партнёра; заявка остаётся, статус → pending_partners.

    Остальные подписи сохраняются. Разрешено: отправитель, администратор,
    либо сам партнёр, чью подпись отзывают.
    """
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    try:
        partner_id = int(partner_auth_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="partner_auth_user_id required") from None
    if partner_id <= 0:
        raise HTTPException(status_code=400, detail="partner_auth_user_id required")

    vid = _viewer_id(viewer)
    conf_repo = PartnerReportConfirmationRepository(session)
    req = await conf_repo.get_request_by_id(rid, load_signatures=True)
    if not req:
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")

    is_submitter = int(req.submitted_by_auth_user_id) == vid
    can_manage = (
        viewer_can_view_all_pending_partner_confirmations(viewer)
        or _viewer_can_see_all_confirmations(viewer)
    )
    is_own_signature = vid == partner_id
    if not is_submitter and not can_manage and not is_own_signature:
        raise HTTPException(
            status_code=403,
            detail="Откатить подпись может отправитель отчёта, администратор или сам подписавший партнёр",
        )

    if not await conf_repo.partner_has_signed(rid, partner_id):
        raise HTTPException(status_code=404, detail="Подпись этого партнёра не найдена")

    await conf_repo.remove_signature(rid, partner_id)
    await conf_repo.mark_pending_partners(rid)
    await session.commit()

    req = await conf_repo.get_request_by_id(rid, load_signatures=True)
    if not req:
        raise HTTPException(status_code=500, detail="internal")
    access_repo = UserProjectAccessRepository(session)
    partners = await list_partner_auth_user_ids_for_project(
        session, access_repo, req.project_id, authorization=authorization
    )
    return _request_to_out(req, partners)


async def list_pending_partner_confirmations(
    session: AsyncSession,
    viewer: dict,
    *,
    authorization: str | None,
    scope: str | None = None,
) -> list[dict]:
    vid = _viewer_id(viewer)
    mode = normalize_partner_pending_scope(scope)
    conf_repo = PartnerReportConfirmationRepository(session)
    access_repo = UserProjectAccessRepository(session)
    await _reconcile_all_completable_pending(
        session, access_repo, authorization=authorization
    )
    candidates = await conf_repo.list_all_pending()

    if mode == "all" and not viewer_can_view_all_pending_partner_confirmations(viewer):
        raise HTTPException(status_code=403, detail="Forbidden")

    visible: list[tuple[object, list[int]]] = []
    team_members_cache: dict[int, set[int]] = {}
    report_users_cache: dict[tuple[str, date, date], set[int]] = {}
    for m in candidates:
        partners = await list_partner_auth_user_ids_for_project(
            session, access_repo, m.project_id, authorization=authorization
        )
        if mode == "mine":
            if vid not in team_members_cache:
                team_members_cache[vid] = await list_team_member_auth_user_ids_for_partner(
                    session, vid
                )
            report_key = (m.project_id, m.date_from, m.date_to)
            if report_key not in report_users_cache:
                report_users_cache[report_key] = await list_report_auth_user_ids_for_project_period(
                    session,
                    project_id=m.project_id,
                    date_from=m.date_from,
                    date_to=m.date_to,
                )
            if not pending_confirmation_visible_for_user_mine(
                m,
                required_partners=partners,
                viewer_id=vid,
                team_member_ids=team_members_cache[vid],
                report_user_ids=report_users_cache[report_key],
            ):
                continue
        visible.append((m, partners))
    entry_counts = await _entry_counts_for_request_rows(
        session, [m for m, _ in visible]
    )
    return [
        _request_to_out(m, partners, entry_count=entry_counts.get(m.id, 0))
        for m, partners in visible
    ]


async def list_confirmed_partner_confirmations(
    session: AsyncSession,
    viewer: dict,
    *,
    authorization: str | None,
    date_from: date | None = None,
    date_to: date | None = None,
    before: date | None = None,
) -> list[dict]:
    vid = _viewer_id(viewer)
    conf_repo = PartnerReportConfirmationRepository(session)
    access_repo = UserProjectAccessRepository(session)
    await _reconcile_all_completable_pending(
        session, access_repo, authorization=authorization
    )
    if _viewer_can_see_all_confirmations(viewer):
        rows = await conf_repo.list_all_fully_confirmed(
            date_from=date_from,
            date_to=date_to,
            before=before,
        )
    else:
        partner_projects = set(
            await list_partner_project_ids_for_viewer(
                session, viewer, authorization=authorization
            )
        )
                                                                          
                                                                                 
        rows = await conf_repo.list_visible_for(
            vid,
            partner_project_ids=partner_projects,
            statuses={"fully_confirmed", "pending_partners"},
            date_from=date_from,
            date_to=date_to,
            before=before,
        )
    summaries = await conf_repo.comments_summary_by_request_ids([m.id for m in rows])
    entry_counts = await _entry_counts_for_request_rows(session, rows)
    out: list[dict] = []
    for m in rows:
        partners = await list_partner_auth_user_ids_for_project(
            session, access_repo, m.project_id, authorization=authorization
        )
        count, last = summaries.get(m.id, (0, None))
        out.append(
            _request_to_out(
                m,
                partners,
                comments_count=count,
                last_comment=last,
                entry_count=entry_counts.get(m.id, 0),
            )
        )
    return out


_COMMENT_TEXT_MAX = 4000
_COMMENTABLE_REQUEST_STATUSES = frozenset({"fully_confirmed", "pending_partners"})


def _ensure_commentable_request_status(status: str | None) -> None:
    normalized = (status or "").strip()
    if normalized not in _COMMENTABLE_REQUEST_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Комментарии доступны только для отчётов на проверке или полностью подтверждённых",
        )


async def _viewer_can_access_confirmation_request(
    session: AsyncSession,
    viewer: dict,
    req,
    *,
    authorization: str | None,
) -> bool:
    """Те же правила видимости, что у списка confirmed."""
    if _viewer_can_see_all_confirmations(viewer):
        return True
    vid = _viewer_id(viewer)
    if int(req.submitted_by_auth_user_id) == vid:
        return True
    signed_ids = {s.partner_auth_user_id for s in (req.signatures or [])}
    if vid in signed_ids:
        return True
    partner_projects = set(
        await list_partner_project_ids_for_viewer(
            session, viewer, authorization=authorization
        )
    )
    return req.project_id in partner_projects


def _normalize_comment_text(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст комментария не может быть пустым")
    if len(text) > _COMMENT_TEXT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"Текст комментария не длиннее {_COMMENT_TEXT_MAX} символов",
        )
    return text


async def list_partner_confirmation_comments(
    session: AsyncSession,
    viewer: dict,
    request_id: str,
    *,
    authorization: str | None,
) -> list[dict]:
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    conf_repo = PartnerReportConfirmationRepository(session)
    req = await conf_repo.get_request_by_id(rid, load_signatures=True)
    if not req:
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")
    if not await _viewer_can_access_confirmation_request(
        session, viewer, req, authorization=authorization
    ):
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")
    comments = await conf_repo.list_comments(rid)
    return [_comment_to_out(c) for c in comments]


async def create_partner_confirmation_comment(
    session: AsyncSession,
    viewer: dict,
    request_id: str,
    *,
    text: str,
    authorization: str | None,
) -> dict:
    rid = (request_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id required")
    body_text = _normalize_comment_text(text)
    vid = _viewer_id(viewer)
    conf_repo = PartnerReportConfirmationRepository(session)
    req = await conf_repo.get_request_by_id(rid, load_signatures=True)
    if not req:
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")
    if not await _viewer_can_access_confirmation_request(
        session, viewer, req, authorization=authorization
    ):
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")
    _ensure_commentable_request_status(getattr(req, "status", None))
    row = await conf_repo.add_comment(
        request_id=rid, auth_user_id=vid, text=body_text
    )
    await session.commit()
    return _comment_to_out(row)


async def invalidate_confirmations_after_row_edit(
    session: AsyncSession,
    snapshot_id: str,
    row_id: str,
) -> None:
    snap_repo = ReportSnapshotRepository(session)
    row = await snap_repo.get_row(snapshot_id, row_id)
    if not row:
        return
    project_id = project_id_from_snapshot_row(row)
    if not project_id:
        return
    conf_repo = PartnerReportConfirmationRepository(session)
    await conf_repo.invalidate_all_for_snapshot_project(snapshot_id, project_id)
    await session.flush()
