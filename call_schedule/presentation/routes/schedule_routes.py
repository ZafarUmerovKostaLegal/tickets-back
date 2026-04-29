

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from infrastructure.config import get_settings
from infrastructure.graph_mailbox import (
    create_calendar_event,
    list_calendar_view,
    list_calendars_for_mailbox,
)
from presentation.deps import get_current_user_id

_log = logging.getLogger(__name__)

router = APIRouter(tags=["call_schedule"])


def _graph_client_error_message(e: httpx.HTTPStatusError) -> str:

    status = e.response.status_code
    code = None
    try:
        j = e.response.json()
        err = j.get("error")
        if isinstance(err, dict):
            code = err.get("code")
    except Exception:
        pass
    tail = f" ({code})" if code else ""
    if status == 403:
        return (
            f"Microsoft Graph: 403{tail}. "
            "Проверьте: 1) Application permissions Calendars.Read / Calendars.ReadWrite и согласие администратора; "
            "2) в Exchange Online не блокирует ли политика доступа приложений к ящику "
            f"{get_settings().call_schedule_mailbox or '…'} — см. docs/call-schedule.md раздел «403 от Graph»."
        )
    return f"Microsoft Graph: {status}{tail}"


def _parse_dt(raw: str) -> datetime:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty datetime")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    d = datetime.fromisoformat(raw)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


@router.get("/calendars", summary="Список календарей почтового ящика")
async def get_calendars(
    _: Annotated[int, Depends(get_current_user_id)],
) -> dict[str, Any]:
    s = get_settings()
    m = s.call_schedule_mailbox
    if not m:
        raise HTTPException(
            status_code=503,
            detail="CALL_SCHEDULE_MAILBOX не задан (например info@kostalegal.com)",
        )
    try:
        cals = await list_calendars_for_mailbox(m)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        _log.warning("Graph calendars: %s", e.response.text[:800])
        raise HTTPException(
            status_code=502,
            detail=_graph_client_error_message(e),
        ) from e
    return {
        "mailbox": m,
        "calendars": cals,
    }


@router.get("/events", summary="События календаря за период")
async def get_events(
    _: Annotated[int, Depends(get_current_user_id)],
    start: str = Query(..., description="Начало периода (ISO 8601, UTC)"),
    end: str = Query(..., description="Конец периода (ISO 8601)"),
    calendar_id: str = Query(
        "default",
        description="id календаря из GET /calendars или default — основной",
        alias="calendarId",
    ),
) -> dict[str, Any]:
    s = get_settings()
    m = s.call_schedule_mailbox
    if not m:
        raise HTTPException(status_code=503, detail="CALL_SCHEDULE_MAILBOX не задан")
    try:
        t0, t1 = _parse_dt(start), _parse_dt(end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Неверные даты: {e}") from e
    if t1 <= t0:
        raise HTTPException(status_code=400, detail="end должен быть позже start")
    try:
        events = await list_calendar_view(m, calendar_id, t0, t1)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        _log.warning("Graph events: %s", e.response.text[:800])
        raise HTTPException(
            status_code=502,
            detail=_graph_client_error_message(e),
        ) from e
    return {
        "mailbox": m,
        "calendarId": calendar_id,
        "start": t0.isoformat(),
        "end": t1.isoformat(),
        "events": events,
    }


class CreateCallBody(BaseModel):


    model_config = {"populate_by_name": True}

    subject: str = Field(..., min_length=1, max_length=500)
    start: str = Field(..., description="Начало (ISO 8601)")
    end: str = Field(..., description="Конец (ISO 8601)")
    body: str | None = Field(None, description="Текст приглашения / заметка (после строки с Join)")
    meeting_url: str | None = Field(
        None,
        description="Обязателен, если не создаёте ссылку Microsoft Teams (см. call_schedule): https:// ссылка на Zoom, Meet, Webex, …",
        min_length=8,
        max_length=2000,
        alias="meetingUrl",
    )
    calendar_id: str | None = Field(
        None,
        description="id календаря; не задано = основной",
        alias="calendarId",
    )
    time_zone: str = Field("UTC", alias="timeZone")

    @field_validator("meeting_url", mode="before")
    @classmethod
    def _strip_meeting_url(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None


@router.post("/events", summary="Создать событие (запись звонка)")
async def post_event(
    body: CreateCallBody,
    _: Annotated[int, Depends(get_current_user_id)],
) -> dict[str, Any]:
    s = get_settings()
    m = s.call_schedule_mailbox
    if not m:
        raise HTTPException(status_code=503, detail="CALL_SCHEDULE_MAILBOX не задан")
    try:
        t0, t1 = _parse_dt(body.start), _parse_dt(body.end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Неверные даты: {e}") from e
    if t1 <= t0:
        raise HTTPException(status_code=400, detail="end должен быть позже start")
    murl = body.meeting_url
    if not s.call_schedule_create_as_teams_meeting:
        if not murl:
            raise HTTPException(
                status_code=400,
                detail="Укажите meetingUrl (https://) на Zoom, Google Meet, Webex и т.д. "
                "Либо в конфигурации включите online meeting Microsoft Teams: "
                "CALL_SCHEDULE_CREATE_AS_TEAMS_MEETING=true (тогда ссылка создастся в Exchange).",
            )
    if murl and not murl.lower().startswith("https://"):
        raise HTTPException(
            status_code=400,
            detail="meetingUrl должен быть ссылкой https://",
        )
    try:
        ev = await create_calendar_event(
            m,
            subject=body.subject.strip(),
            start=t0,
            end=t1,
            body=body.body,
            meeting_url=murl,
            calendar_id=body.calendar_id,
            time_zone=body.time_zone or "UTC",
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        _log.warning("Graph create: %s", e.response.text[:800])
        raise HTTPException(
            status_code=502,
            detail=_graph_client_error_message(e),
        ) from e
    return ev
