from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.views import TASK_STATUSES, TASK_VIEWS, task_in_view
from infrastructure.database import get_session
from infrastructure.models import LegalHubTaskModel
from presentation.deps import require_user

router = APIRouter(tags=["legal-hub"])


class TaskWrite(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=8000)
    status: str = "in_progress"
    assigneeUserId: int | None = None
    reviewerUserId: int | None = None
    projectLabel: str = Field(default="", max_length=300)
    dueOn: date | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in TASK_STATUSES:
            raise ValueError("Неизвестный статус задачи")
        return value

    @field_validator("title", "description", "projectLabel")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    status: str | None = None
    assigneeUserId: int | None = None
    reviewerUserId: int | None = None
    projectLabel: str | None = Field(default=None, max_length=300)
    dueOn: date | None = None
    clearReviewer: bool = False
    clearDueOn: bool = False

    @field_validator("status")
    @classmethod
    def _status(cls, value: str | None) -> str | None:
        if value is not None and value not in TASK_STATUSES:
            raise ValueError("Неизвестный статус задачи")
        return value

    @field_validator("title", "description", "projectLabel")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


def _user_id(user: dict) -> int:
    return int(user["id"])


def _positive_user(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise HTTPException(status_code=400, detail=f"Некорректный {label}")
    return value


def _payload(row: LegalHubTaskModel) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "assigneeUserId": row.assignee_user_id,
        "reviewerUserId": row.reviewer_user_id,
        "createdByUserId": row.created_by_user_id,
        "projectLabel": row.project_label,
        "dueOn": row.due_on.isoformat() if row.due_on else None,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


async def _all_tasks(session: AsyncSession) -> list[LegalHubTaskModel]:
    rows = await session.scalars(
        select(LegalHubTaskModel).order_by(LegalHubTaskModel.updated_at.desc(), LegalHubTaskModel.id.desc())
    )
    return list(rows)


def _visible(rows: list[LegalHubTaskModel], user_id: int, view: str) -> list[LegalHubTaskModel]:
    return [
        row for row in rows
        if task_in_view(
            assignee_user_id=row.assignee_user_id,
            reviewer_user_id=row.reviewer_user_id,
            user_id=user_id,
            view=view,
        )
    ]


@router.get("/summary")
async def task_summary(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    uid = _user_id(user)
    rows = await _all_tasks(session)
    return {
        "mine": len(_visible(rows, uid, "mine")),
        "review": len(_visible(rows, uid, "review")),
        "team": len(rows),
    }


@router.get("/team")
async def team_workload(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    rows = await _all_tasks(session)
    grouped: dict[int, Counter[str]] = {}
    for row in rows:
        grouped.setdefault(row.assignee_user_id, Counter())[row.status] += 1
    items = [
        {
            "userId": user_id,
            "taskCount": sum(counts.values()),
            "byStatus": dict(counts),
        }
        for user_id, counts in sorted(grouped.items(), key=lambda item: (-sum(item[1].values()), item[0]))
    ]
    return {"items": items}


@router.get("/tasks")
async def list_tasks(
    view: str = Query(default="mine"),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    if view not in TASK_VIEWS:
        raise HTTPException(status_code=400, detail="Неизвестный раздел")
    rows = _visible(await _all_tasks(session), _user_id(user), view)
    return {"items": [_payload(row) for row in rows]}


@router.post("/tasks", status_code=201)
async def create_task(
    body: TaskWrite,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    if not body.title:
        raise HTTPException(status_code=400, detail="Укажите название задачи")
    uid = _user_id(user)
    assignee = _positive_user(body.assigneeUserId, "исполнитель") or uid
    reviewer = _positive_user(body.reviewerUserId, "проверяющий")
    now = datetime.now(timezone.utc)
    row = LegalHubTaskModel(
        id=str(uuid4()),
        title=body.title,
        description=body.description,
        status=body.status,
        assignee_user_id=assignee,
        reviewer_user_id=reviewer,
        created_by_user_id=uid,
        project_label=body.projectLabel,
        due_on=body.dueOn,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _payload(row)


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    row = await session.get(LegalHubTaskModel, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return _payload(row)


@router.patch("/tasks/{task_id}")
async def patch_task(
    task_id: str,
    body: TaskPatch,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    row = await session.get(LegalHubTaskModel, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    data = body.model_dump(exclude_unset=True)
    if "title" in data:
        if not data["title"]:
            raise HTTPException(status_code=400, detail="Укажите название задачи")
        row.title = data["title"]
    if "description" in data:
        row.description = data["description"]
    if "status" in data and data["status"] is not None:
        row.status = data["status"]
    if "assigneeUserId" in data:
        assignee = _positive_user(data["assigneeUserId"], "исполнитель")
        if assignee is None:
            raise HTTPException(status_code=400, detail="Некорректный исполнитель")
        row.assignee_user_id = assignee
    if body.clearReviewer:
        row.reviewer_user_id = None
    elif "reviewerUserId" in data:
        row.reviewer_user_id = _positive_user(data["reviewerUserId"], "проверяющий")
    if "projectLabel" in data:
        row.project_label = data["projectLabel"]
    if body.clearDueOn:
        row.due_on = None
    elif "dueOn" in data:
        row.due_on = data["dueOn"]
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return _payload(row)
