from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.task_mail import notify_assignee
from application.views import TASK_STATUSES, TASK_VIEWS, task_in_view
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import (
    LegalHubTaskCheckModel,
    LegalHubTaskCheckNoteModel,
    LegalHubTaskCommentModel,
    LegalHubTaskEventModel,
    LegalHubTaskFileModel,
    LegalHubTaskModel,
)
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
    projectId: str = Field(default="", max_length=80)
    priority: str = "normal"
    internalOn: date | None = None
    instructions: str = Field(default="", max_length=8000)
    approval: bool = False
    boardColumn: str = Field(default="", max_length=40)
    assigneeName: str = Field(default="", max_length=200)
    assigneeCode: str = Field(default="", max_length=16)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in TASK_STATUSES:
            raise ValueError("Неизвестный статус задачи")
        return value

    @field_validator("title", "description", "projectLabel", "projectId", "instructions", "boardColumn", "assigneeName", "assigneeCode", "priority")
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
    internalOn: date | None = None
    reviewOn: date | None = None
    boardColumn: str | None = Field(default=None, max_length=40)
    approval: bool | None = None
    archived: bool | None = None
    clearReviewer: bool = False
    clearDueOn: bool = False
    clearInternalOn: bool = False
    clearReviewOn: bool = False
    notifyDates: bool = False
    notifyReview: bool = False

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


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class CheckIn(BaseModel):
    label: str = Field(min_length=1, max_length=500)

    @field_validator("label")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class CheckPatch(BaseModel):
    done: bool | None = None
    label: str | None = Field(default=None, max_length=500)


def _user_id(user: dict) -> int:
    return int(user["id"])


def _positive_user(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise HTTPException(status_code=400, detail=f"Некорректный {label}")
    return value


def _display(user: dict) -> str:
    for key in ("display_name", "name", "email"):
        value = user.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(user.get("id", ""))


def _file_kind(name: str) -> str:
    return "pdf" if name.lower().endswith(".pdf") else "docx"


def _file_size(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} KB"
    return f"{size / (1024 * 1024):.1f} MB"


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
        "projectId": row.project_id or "",
        "priority": row.priority or "normal",
        "internalOn": row.internal_on.isoformat() if row.internal_on else None,
        "reviewOn": row.review_on.isoformat() if row.review_on else None,
        "instructions": row.instructions or "",
        "approval": bool(row.approval),
        "boardColumn": row.board_column or "",
        "important": bool(row.important),
        "archived": bool(row.archived),
        "assigneeName": row.assignee_name or "",
        "assigneeCode": row.assignee_code or "",
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
        project_id=body.projectId,
        priority=body.priority or "normal",
        internal_on=body.internalOn,
        instructions=body.instructions or body.description,
        approval=body.approval,
        board_column=body.boardColumn,
        assignee_name=body.assigneeName,
        assignee_code=body.assigneeCode,
        due_on=body.dueOn,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.add(LegalHubTaskEventModel(
        id=str(uuid4()),
        task_id=row.id,
        kind="created",
        actor_name=_display(user),
        text=body.title,
        created_at=now,
    ))
    await session.commit()
    await session.refresh(row)
    await notify_assignee(
        get_settings(),
        authorization=str(user.get("authorization") or ""),
        assignee_user_id=assignee,
        actor=user,
        title=row.title,
        project=row.project_label,
        due_on=row.due_on.isoformat() if row.due_on else None,
        kind="assigned",
    )
    return _payload(row)


async def _detail(session: AsyncSession, row: LegalHubTaskModel) -> dict:
    comments = list(await session.scalars(
        select(LegalHubTaskCommentModel)
        .where(LegalHubTaskCommentModel.task_id == row.id)
        .order_by(LegalHubTaskCommentModel.created_at.asc())
    ))
    files = list(await session.scalars(
        select(LegalHubTaskFileModel)
        .where(LegalHubTaskFileModel.task_id == row.id)
        .order_by(LegalHubTaskFileModel.created_at.asc())
    ))
    events = list(await session.scalars(
        select(LegalHubTaskEventModel)
        .where(LegalHubTaskEventModel.task_id == row.id)
        .order_by(LegalHubTaskEventModel.created_at.asc())
    ))
    checks = list(await session.scalars(
        select(LegalHubTaskCheckModel)
        .where(LegalHubTaskCheckModel.task_id == row.id)
        .order_by(LegalHubTaskCheckModel.position.asc(), LegalHubTaskCheckModel.created_at.asc())
    ))
    notes = list(await session.scalars(
        select(LegalHubTaskCheckNoteModel)
        .where(LegalHubTaskCheckNoteModel.check_id.in_([item.id for item in checks] or [""]))
        .order_by(LegalHubTaskCheckNoteModel.created_at.asc())
    )) if checks else []
    payload = _payload(row)

    def _file(item: LegalHubTaskFileModel) -> dict:
        return {
            "id": item.id,
            "name": item.name,
            "kind": _file_kind(item.name),
            "size": _file_size(item.size_bytes),
            "createdAt": item.created_at.isoformat(),
            "checkId": item.check_id,
            "commentId": item.comment_id,
        }

    payload["comments"] = [
        {
            "id": item.id,
            "authorUserId": item.author_user_id,
            "author": item.author_name,
            "text": item.text,
            "createdAt": item.created_at.isoformat(),
            "files": [_file(item_file) for item_file in files if item_file.comment_id == item.id],
        }
        for item in comments
    ]
    payload["files"] = [_file(item) for item in files]
    payload["checks"] = [
        {
            "id": item.id,
            "label": item.label,
            "done": bool(item.done),
            "notes": [{"id": note.id, "text": note.text} for note in notes if note.check_id == item.id],
            "files": [{"id": item_file.id, "name": item_file.name} for item_file in files if item_file.check_id == item.id],
        }
        for item in checks
    ]
    payload["history"] = [
        {
            "id": item.id,
            "kind": item.kind,
            "actor": item.actor_name,
            "text": item.text,
            "createdAt": item.created_at.isoformat(),
        }
        for item in events
    ]
    return payload


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    row = await session.get(LegalHubTaskModel, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return await _detail(session, row)


@router.patch("/tasks/{task_id}")
async def patch_task(
    task_id: str,
    body: TaskPatch,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    row = await session.get(LegalHubTaskModel, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    data = body.model_dump(exclude_unset=True)
    before = (
        row.internal_on.isoformat() if row.internal_on else None,
        row.review_on.isoformat() if row.review_on else None,
        row.due_on.isoformat() if row.due_on else None,
    )
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
    if "boardColumn" in data and data["boardColumn"] is not None:
        row.board_column = data["boardColumn"]
    if "approval" in data and data["approval"] is not None:
        row.approval = bool(data["approval"])
    if "archived" in data and data["archived"] is not None:
        row.archived = bool(data["archived"])
    if body.clearInternalOn:
        row.internal_on = None
    elif "internalOn" in data:
        row.internal_on = data["internalOn"]
    if body.clearReviewOn:
        row.review_on = None
    elif "reviewOn" in data:
        row.review_on = data["reviewOn"]
    if body.clearDueOn:
        row.due_on = None
    elif "dueOn" in data:
        row.due_on = data["dueOn"]
    now = datetime.now(timezone.utc)
    if body.notifyReview:
        file_count = len(list(await session.scalars(
            select(LegalHubTaskFileModel.id).where(LegalHubTaskFileModel.task_id == row.id)
        )))
        if file_count == 0:
            raise HTTPException(status_code=400, detail="Добавьте файл, чтобы отправить на проверку")
        if row.reviewer_user_id is None:
            raise HTTPException(status_code=400, detail="Назначьте проверяющего")
        session.add(LegalHubTaskEventModel(
            id=str(uuid4()),
            task_id=row.id,
            kind="review",
            actor_name=_display(user),
            text=row.title,
            created_at=now,
        ))
    after = (
        row.internal_on.isoformat() if row.internal_on else None,
        row.review_on.isoformat() if row.review_on else None,
        row.due_on.isoformat() if row.due_on else None,
    )
    row.updated_at = now
    await session.commit()
    await session.refresh(row)
    if body.notifyReview and row.reviewer_user_id is not None:
        await notify_assignee(
            get_settings(),
            authorization=str(user.get("authorization") or ""),
            assignee_user_id=row.reviewer_user_id,
            actor=user,
            title=row.title,
            project=row.project_label,
            due_on=row.due_on.isoformat() if row.due_on else None,
            kind="review",
        )
    if body.notifyDates and before != after:
        summary = "\n".join(part for part in (
            f"Внутренний срок: {after[0]}" if after[0] else "",
            f"Срок проверки: {after[1]}" if after[1] else "",
            f"Срок клиента: {after[2]}" if after[2] else "",
        ) if part)
        recipients = {row.assignee_user_id, row.reviewer_user_id or 0}
        recipients.discard(_user_id(user))
        recipients.discard(0)
        for recipient in recipients:
            await notify_assignee(
                get_settings(),
                authorization=str(user.get("authorization") or ""),
                assignee_user_id=recipient,
                actor=user,
                title=row.title,
                project=row.project_label,
                due_on=summary or None,
                kind="dates",
            )
    return _payload(row)


MAX_FILE_BYTES = 15 * 1024 * 1024


async def _task_or_404(session: AsyncSession, task_id: str) -> LegalHubTaskModel:
    row = await session.get(LegalHubTaskModel, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return row


@router.post("/tasks/{task_id}/comments", status_code=201)
async def add_comment(
    task_id: str,
    body: CommentIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    if not body.text:
        raise HTTPException(status_code=400, detail="Напишите сообщение")
    row = await _task_or_404(session, task_id)
    now = datetime.now(timezone.utc)
    comment = LegalHubTaskCommentModel(
        id=str(uuid4()),
        task_id=row.id,
        author_user_id=_user_id(user),
        author_name=_display(user),
        text=body.text,
        created_at=now,
    )
    session.add(comment)
    session.add(LegalHubTaskEventModel(
        id=str(uuid4()),
        task_id=row.id,
        kind="comment",
        actor_name=_display(user),
        text=body.text[:180],
        created_at=now,
    ))
    row.updated_at = now
    await session.commit()
    recipients = {row.assignee_user_id, row.created_by_user_id}
    recipients.discard(_user_id(user))
    for recipient in recipients:
        await notify_assignee(
            get_settings(),
            authorization=str(user.get("authorization") or ""),
            assignee_user_id=recipient,
            actor=user,
            title=row.title,
            project=row.project_label,
            due_on=None,
            kind="comment",
        )
    detail = await _detail(session, row)
    detail["createdId"] = comment.id
    return detail


@router.post("/tasks/{task_id}/files", status_code=201)
async def add_files(
    task_id: str,
    files: list[UploadFile] = File(...),
    check_id: str | None = Form(default=None),
    comment_id: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    row = await _task_or_404(session, task_id)
    linked_check = (check_id or "").strip() or None
    linked_comment = (comment_id or "").strip() or None
    if linked_check is not None:
        check = await session.get(LegalHubTaskCheckModel, linked_check)
        if check is None or check.task_id != row.id:
            raise HTTPException(status_code=400, detail="Пункт чеклиста не найден")
    if linked_comment is not None:
        comment = await session.get(LegalHubTaskCommentModel, linked_comment)
        if comment is None or comment.task_id != row.id:
            raise HTTPException(status_code=400, detail="Комментарий не найден")
    now = datetime.now(timezone.utc)
    stored = 0
    for upload in files:
        content = await upload.read()
        if not content:
            continue
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail="Файл больше 15 МБ")
        name = (upload.filename or "file").strip()[:300] or "file"
        session.add(LegalHubTaskFileModel(
            id=str(uuid4()),
            task_id=row.id,
            name=name,
            content_type=(upload.content_type or "application/octet-stream")[:120],
            size_bytes=len(content),
            content=content,
            created_by_user_id=_user_id(user),
            created_at=now,
            check_id=linked_check,
            comment_id=linked_comment,
        ))
        session.add(LegalHubTaskEventModel(
            id=str(uuid4()),
            task_id=row.id,
            kind="file",
            actor_name=_display(user),
            text=name,
            created_at=now,
        ))
        stored += 1
    if stored == 0:
        raise HTTPException(status_code=400, detail="Выберите файл")
    row.updated_at = now
    await session.commit()
    return await _detail(session, row)


@router.get("/tasks/{task_id}/files/{file_id}")
async def download_file(
    task_id: str,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    item = await session.get(LegalHubTaskFileModel, file_id)
    if item is None or item.task_id != task_id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return Response(
        content=item.content,
        media_type=item.content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(item.name)}"},
    )


@router.delete("/tasks/{task_id}/files/{file_id}")
async def delete_file(
    task_id: str,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    item = await session.get(LegalHubTaskFileModel, file_id)
    if item is None or item.task_id != task_id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    await session.delete(item)
    await session.commit()
    return {"ok": True}


async def _check_or_404(session: AsyncSession, task_id: str, check_id: str) -> LegalHubTaskCheckModel:
    item = await session.get(LegalHubTaskCheckModel, check_id)
    if item is None or item.task_id != task_id:
        raise HTTPException(status_code=404, detail="Пункт чеклиста не найден")
    return item


@router.post("/tasks/{task_id}/checks", status_code=201)
async def add_check(
    task_id: str,
    body: CheckIn,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    if not body.label:
        raise HTTPException(status_code=400, detail="Напишите пункт")
    row = await _task_or_404(session, task_id)
    existing = list(await session.scalars(
        select(LegalHubTaskCheckModel.id).where(LegalHubTaskCheckModel.task_id == row.id)
    ))
    now = datetime.now(timezone.utc)
    session.add(LegalHubTaskCheckModel(
        id=str(uuid4()),
        task_id=row.id,
        label=body.label,
        done=False,
        position=len(existing),
        created_at=now,
    ))
    row.updated_at = now
    await session.commit()
    return await _detail(session, row)


@router.patch("/tasks/{task_id}/checks/{check_id}")
async def patch_check(
    task_id: str,
    check_id: str,
    body: CheckPatch,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    item = await _check_or_404(session, task_id, check_id)
    if body.done is not None:
        item.done = body.done
    if body.label is not None and body.label.strip():
        item.label = body.label.strip()
    await session.commit()
    row = await _task_or_404(session, task_id)
    return await _detail(session, row)


@router.post("/tasks/{task_id}/checks/{check_id}/notes", status_code=201)
async def add_check_note(
    task_id: str,
    check_id: str,
    body: CommentIn,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    if not body.text:
        raise HTTPException(status_code=400, detail="Напишите комментарий")
    await _check_or_404(session, task_id, check_id)
    session.add(LegalHubTaskCheckNoteModel(
        id=str(uuid4()),
        check_id=check_id,
        text=body.text,
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()
    row = await _task_or_404(session, task_id)
    return await _detail(session, row)


@router.delete("/tasks/{task_id}/checks/{check_id}/notes/{note_id}")
async def delete_check_note(
    task_id: str,
    check_id: str,
    note_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_user),
):
    await _check_or_404(session, task_id, check_id)
    note = await session.get(LegalHubTaskCheckNoteModel, note_id)
    if note is None or note.check_id != check_id:
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    await session.delete(note)
    await session.commit()
    row = await _task_or_404(session, task_id)
    return await _detail(session, row)
