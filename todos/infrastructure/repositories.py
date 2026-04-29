from datetime import datetime, timezone

from collections import defaultdict
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from application.ports import HealthRepositoryPort
from infrastructure.config import get_settings
from infrastructure.file_storage import save_todo_card_file
from infrastructure.models import (
    OutlookCalendarTokenModel,
    TodoBoardLabelModel,
    TodoBoardModel,
    TodoCardAttachmentModel,
    TodoCardChecklistItemModel,
    TodoCardCommentModel,
    TodoCardLabelModel,
    TodoCardModel,
    TodoCardParticipantModel,
    TodoColumnModel,
)


class HealthRepository(HealthRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def check(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


class OutlookCalendarTokenRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_user_id(self, user_id: int) -> OutlookCalendarTokenModel | None:
        r = await self._session.execute(
            select(OutlookCalendarTokenModel).where(
                OutlookCalendarTokenModel.user_id == user_id
            )
        )
        return r.scalars().one_or_none()

    async def upsert(
        self,
        *,
        user_id: int,
        access_token: str,
        refresh_token: str,
        expires_at: datetime | None,
    ) -> None:
        row = await self.get_by_user_id(user_id)
        if row:
            row.access_token = access_token
            row.refresh_token = refresh_token
            row.expires_at = expires_at
            self._session.add(row)
        else:
            self._session.add(
                OutlookCalendarTokenModel(
                    user_id=user_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at,
                )
            )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_DEFAULT_KANBAN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Сегодня", "#7c3aed"),
    ("На этой неделе", "#2563eb"),
    ("Позже", "#ea580c"),
)


class KanbanRepository:


    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_board_row(self, user_id: int) -> TodoBoardModel | None:
        r = await self._session.execute(
            select(TodoBoardModel).where(TodoBoardModel.user_id == user_id)
        )
        return r.scalars().one_or_none()

    async def ensure_board(self, user_id: int) -> TodoBoardModel:
        row = await self.get_board_row(user_id)
        if row:
            return row
        now = _utc_now()
        row = TodoBoardModel(
            user_id=user_id,
            background_url=None,
            created_at=now,
            updated_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        for i, (title, color) in enumerate(_DEFAULT_KANBAN_COLUMNS):
            self._session.add(
                TodoColumnModel(
                    board_id=row.id,
                    title=title,
                    position=i,
                    color=color,
                    is_collapsed=False,
                    created_at=now,
                    updated_at=None,
                )
            )
        return row

    async def _columns_for_board(self, board_id: int) -> list[TodoColumnModel]:
        r = await self._session.execute(
            select(TodoColumnModel).where(TodoColumnModel.board_id == board_id)
        )
        cols = list(r.scalars().all())
        cols.sort(key=lambda c: (c.position, c.id))
        return cols

    async def _cards_for_column(self, column_id: int) -> list[TodoCardModel]:
        r = await self._session.execute(
            select(TodoCardModel).where(
                TodoCardModel.column_id == column_id,
                TodoCardModel.is_archived.is_(False),
            )
        )
        cards = list(r.scalars().all())
        cards.sort(key=lambda x: (x.position, x.id))
        return cards

    async def _cards_for_column_all(self, column_id: int) -> list[TodoCardModel]:

        r = await self._session.execute(
            select(TodoCardModel).where(TodoCardModel.column_id == column_id)
        )
        cards = list(r.scalars().all())
        cards.sort(key=lambda x: (x.position, x.id))
        return cards

    async def get_column_if_owned(
        self,
        user_id: int,
        column_id: int,
    ) -> TodoColumnModel | None:
        r = await self._session.execute(
            select(TodoColumnModel)
            .join(TodoBoardModel, TodoColumnModel.board_id == TodoBoardModel.id)
            .where(
                TodoBoardModel.user_id == user_id,
                TodoColumnModel.id == column_id,
            )
        )
        return r.scalars().one_or_none()

    async def get_card_if_owned(self, user_id: int, card_id: int) -> TodoCardModel | None:
        r = await self._session.execute(
            select(TodoCardModel)
            .join(TodoColumnModel, TodoCardModel.column_id == TodoColumnModel.id)
            .join(TodoBoardModel, TodoColumnModel.board_id == TodoBoardModel.id)
            .where(
                TodoBoardModel.user_id == user_id,
                TodoCardModel.id == card_id,
            )
        )
        return r.scalars().one_or_none()

    async def patch_board(
        self,
        user_id: int,
        *,
        background_url: str | None,
    ) -> TodoBoardModel:
        row = await self.ensure_board(user_id)
        row.background_url = background_url
        row.updated_at = _utc_now()
        self._session.add(row)
        return row

    async def add_column(
        self,
        user_id: int,
        *,
        title: str,
        color: str,
        insert_at: int | None,
        is_collapsed: bool = False,
    ) -> TodoColumnModel:
        board = await self.ensure_board(user_id)
        cols = await self._columns_for_board(board.id)
        n = len(cols)
        pos = n if insert_at is None else max(0, min(int(insert_at), n))
        now = _utc_now()
        for c in cols:
            if c.position >= pos:
                c.position += 1
                c.updated_at = now
                self._session.add(c)
        col = TodoColumnModel(
            board_id=board.id,
            title=title.strip(),
            position=pos,
            color=(color or "#6b7280").strip()[:32],
            is_collapsed=bool(is_collapsed),
            created_at=now,
            updated_at=None,
        )
        self._session.add(col)
        await self._session.flush()
        return col

    async def list_board_labels(self, user_id: int) -> list[TodoBoardLabelModel]:
        board = await self.ensure_board(user_id)
        r = await self._session.execute(
            select(TodoBoardLabelModel).where(TodoBoardLabelModel.board_id == board.id)
        )
        rows = list(r.scalars().all())
        rows.sort(key=lambda x: (x.position, x.id))
        return rows

    async def add_board_label(self, user_id: int, *, title: str, color: str) -> TodoBoardLabelModel:
        board = await self.ensure_board(user_id)
        existing = await self.list_board_labels(user_id)
        n = len(existing)
        now = _utc_now()
        row = TodoBoardLabelModel(
            board_id=board.id,
            title=title.strip()[:200],
            color=(color or "#6b7280").strip()[:32],
            position=n,
            created_at=now,
            updated_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update_board_label(
        self,
        user_id: int,
        label_id: int,
        *,
        title: str | None,
        color: str | None,
    ) -> TodoBoardLabelModel | None:
        board = await self.ensure_board(user_id)
        r = await self._session.execute(
            select(TodoBoardLabelModel).where(
                TodoBoardLabelModel.id == label_id,
                TodoBoardLabelModel.board_id == board.id,
            )
        )
        row = r.scalars().one_or_none()
        if not row:
            return None
        now = _utc_now()
        if title is not None:
            row.title = title.strip()[:200]
        if color is not None:
            row.color = color.strip()[:32]
        row.updated_at = now
        self._session.add(row)
        return row

    async def delete_board_label(self, user_id: int, label_id: int) -> bool:
        board = await self.ensure_board(user_id)
        r = await self._session.execute(
            select(TodoBoardLabelModel).where(
                TodoBoardLabelModel.id == label_id,
                TodoBoardLabelModel.board_id == board.id,
            )
        )
        row = r.scalars().one_or_none()
        if not row:
            return False
        await self._session.execute(
            delete(TodoBoardLabelModel).where(TodoBoardLabelModel.id == label_id)
        )
        await self._session.flush()
        return True

    async def batch_card_label_payload(
        self,
        card_ids: list[int],
    ) -> dict[int, list[tuple[int, str, str]]]:

        if not card_ids:
            return {}
        r = await self._session.execute(
            select(
                TodoCardLabelModel.card_id,
                TodoBoardLabelModel.id,
                TodoBoardLabelModel.title,
                TodoBoardLabelModel.color,
            )
            .join(TodoBoardLabelModel, TodoCardLabelModel.label_id == TodoBoardLabelModel.id)
            .where(TodoCardLabelModel.card_id.in_(card_ids))
        )
        out: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
        for card_id, lid, title, color in r.all():
            out[int(card_id)].append((int(lid), str(title), str(color)))
        for k in out:
            out[k].sort(key=lambda x: x[0])
        return dict(out)

    async def batch_checklist_items(
        self,
        card_ids: list[int],
    ) -> dict[int, list[TodoCardChecklistItemModel]]:
        if not card_ids:
            return {}
        r = await self._session.execute(
            select(TodoCardChecklistItemModel).where(
                TodoCardChecklistItemModel.card_id.in_(card_ids)
            )
        )
        items = list(r.scalars().all())
        out: dict[int, list[TodoCardChecklistItemModel]] = defaultdict(list)
        for it in items:
            out[it.card_id].append(it)
        for k in out:
            out[k].sort(key=lambda x: (x.position, x.id))
        return dict(out)

    async def batch_participant_ids(self, card_ids: list[int]) -> dict[int, list[int]]:
        if not card_ids:
            return {}
        r = await self._session.execute(
            select(TodoCardParticipantModel).where(
                TodoCardParticipantModel.card_id.in_(card_ids)
            )
        )
        rows = list(r.scalars().all())
        out: dict[int, list[int]] = defaultdict(list)
        for p in rows:
            out[p.card_id].append(p.user_id)
        for k in out:
            out[k].sort()
        return dict(out)

    async def batch_attachments(
        self,
        card_ids: list[int],
    ) -> dict[int, list[TodoCardAttachmentModel]]:
        if not card_ids:
            return {}
        r = await self._session.execute(
            select(TodoCardAttachmentModel).where(
                TodoCardAttachmentModel.card_id.in_(card_ids)
            )
        )
        rows = list(r.scalars().all())
        out: dict[int, list[TodoCardAttachmentModel]] = defaultdict(list)
        for a in rows:
            out[a.card_id].append(a)
        for k in out:
            out[k].sort(key=lambda x: (x.uploaded_at, x.id))
        return dict(out)

    async def batch_comments(
        self,
        card_ids: list[int],
        *,
        limit_per_card: int = 100,
    ) -> dict[int, list[TodoCardCommentModel]]:
        if not card_ids:
            return {}
        r = await self._session.execute(
            select(TodoCardCommentModel)
            .where(TodoCardCommentModel.card_id.in_(card_ids))
            .order_by(
                TodoCardCommentModel.card_id.asc(),
                TodoCardCommentModel.created_at.desc(),
                TodoCardCommentModel.id.desc(),
            )
        )
        rows = list(r.scalars().all())
        out: dict[int, list[TodoCardCommentModel]] = defaultdict(list)
        for c in rows:
            lst = out[c.card_id]
            if len(lst) < limit_per_card:
                lst.append(c)
        for k in out:
            out[k].reverse()
        return dict(out)

    async def replace_card_labels(
        self,
        user_id: int,
        card_id: int,
        label_ids: list[int],
    ) -> bool:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return False
        col = await self.get_column_if_owned(user_id, card.column_id)
        if not col:
            return False
        board_id = col.board_id
        uniq = sorted(set(label_ids))
        if not uniq:
            await self._session.execute(
                delete(TodoCardLabelModel).where(TodoCardLabelModel.card_id == card_id)
            )
            return True
        r = await self._session.execute(
            select(TodoBoardLabelModel.id).where(
                TodoBoardLabelModel.board_id == board_id,
                TodoBoardLabelModel.id.in_(uniq),
            )
        )
        found = {int(x) for x in r.scalars().all()}
        if found != set(uniq):
            return False
        await self._session.execute(
            delete(TodoCardLabelModel).where(TodoCardLabelModel.card_id == card_id)
        )
        for lid in uniq:
            self._session.add(TodoCardLabelModel(card_id=card_id, label_id=lid))
        return True

    async def replace_card_participants(
        self,
        user_id: int,
        card_id: int,
        participant_user_ids: list[int],
    ) -> bool:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return False
        uniq = sorted(set(participant_user_ids))
        await self._session.execute(
            delete(TodoCardParticipantModel).where(
                TodoCardParticipantModel.card_id == card_id
            )
        )
        for uid in uniq:
            self._session.add(TodoCardParticipantModel(card_id=card_id, user_id=uid))
        return True

    async def add_checklist_item(
        self,
        user_id: int,
        card_id: int,
        *,
        title: str,
        insert_at: int | None,
    ) -> TodoCardChecklistItemModel | None:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return None
        items = await self._checklist_for_card(card_id)
        n = len(items)
        pos = n if insert_at is None else max(0, min(int(insert_at), n))
        now = _utc_now()
        for it in items:
            if it.position >= pos:
                it.position += 1
                it.updated_at = now
                self._session.add(it)
        row = TodoCardChecklistItemModel(
            card_id=card_id,
            title=title.strip()[:500],
            is_done=False,
            position=pos,
            created_at=now,
            updated_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _checklist_for_card(self, card_id: int) -> list[TodoCardChecklistItemModel]:
        r = await self._session.execute(
            select(TodoCardChecklistItemModel).where(
                TodoCardChecklistItemModel.card_id == card_id
            )
        )
        items = list(r.scalars().all())
        items.sort(key=lambda x: (x.position, x.id))
        return items

    async def update_checklist_item(
        self,
        user_id: int,
        card_id: int,
        item_id: int,
        *,
        title: str | None,
        is_done: bool | None,
    ) -> TodoCardChecklistItemModel | None:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return None
        r = await self._session.execute(
            select(TodoCardChecklistItemModel).where(
                TodoCardChecklistItemModel.id == item_id,
                TodoCardChecklistItemModel.card_id == card_id,
            )
        )
        row = r.scalars().one_or_none()
        if not row:
            return None
        now = _utc_now()
        if title is not None:
            row.title = title.strip()[:500]
        if is_done is not None:
            row.is_done = bool(is_done)
        row.updated_at = now
        self._session.add(row)
        return row

    async def delete_checklist_item(
        self,
        user_id: int,
        card_id: int,
        item_id: int,
    ) -> bool:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return False
        r = await self._session.execute(
            select(TodoCardChecklistItemModel).where(
                TodoCardChecklistItemModel.id == item_id,
                TodoCardChecklistItemModel.card_id == card_id,
            )
        )
        row = r.scalars().one_or_none()
        if not row:
            return False
        await self._session.execute(
            delete(TodoCardChecklistItemModel).where(
                TodoCardChecklistItemModel.id == item_id
            )
        )
        await self._session.flush()
        items = await self._checklist_for_card(card_id)
        now = _utc_now()
        for i, it in enumerate(items):
            if it.position != i:
                it.position = i
                it.updated_at = now
                self._session.add(it)
        return True

    async def reorder_checklist_items(
        self,
        user_id: int,
        card_id: int,
        ordered_item_ids: list[int],
    ) -> bool:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return False
        items = await self._checklist_for_card(card_id)
        existing = {it.id for it in items}
        if set(ordered_item_ids) != existing or len(ordered_item_ids) != len(existing):
            return False
        now = _utc_now()
        for i, iid in enumerate(ordered_item_ids):
            for it in items:
                if it.id == iid and it.position != i:
                    it.position = i
                    it.updated_at = now
                    self._session.add(it)
        return True

    async def add_card_attachment(
        self,
        user_id: int,
        card_id: int,
        *,
        original_filename: str,
        content: bytes,
        mime_type: str | None,
    ) -> TodoCardAttachmentModel | None:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return None
        storage_key, size = save_todo_card_file(
            owner_user_id=user_id,
            card_id=card_id,
            original_filename=original_filename,
            content=content,
        )
        now = _utc_now()
        row = TodoCardAttachmentModel(
            card_id=card_id,
            storage_key=storage_key,
            original_filename=original_filename[:500],
            mime_type=(mime_type or "")[:200] or None,
            size_bytes=size,
            uploaded_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def delete_card_attachment(
        self,
        user_id: int,
        card_id: int,
        attachment_id: int,
    ) -> bool:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return False
        r = await self._session.execute(
            select(TodoCardAttachmentModel).where(
                TodoCardAttachmentModel.id == attachment_id,
                TodoCardAttachmentModel.card_id == card_id,
            )
        )
        row = r.scalars().one_or_none()
        if not row:
            return False
        base = Path(get_settings().media_path).resolve()
        p = (base / row.storage_key).resolve()
        if str(p).startswith(str(base)) and p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
        await self._session.execute(
            delete(TodoCardAttachmentModel).where(TodoCardAttachmentModel.id == attachment_id)
        )
        return True

    async def add_card_comment(
        self,
        user_id: int,
        card_id: int,
        *,
        body: str,
    ) -> TodoCardCommentModel | None:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return None
        now = _utc_now()
        row = TodoCardCommentModel(
            card_id=card_id,
            user_id=user_id,
            body=body.strip(),
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update_column(
        self,
        user_id: int,
        column_id: int,
        *,
        title: str | None,
        color: str | None,
        is_collapsed: bool | None,
    ) -> TodoColumnModel | None:
        col = await self.get_column_if_owned(user_id, column_id)
        if not col:
            return None
        now = _utc_now()
        if title is not None:
            col.title = title.strip()
        if color is not None:
            col.color = color.strip()[:32]
        if is_collapsed is not None:
            col.is_collapsed = bool(is_collapsed)
        col.updated_at = now
        self._session.add(col)
        return col

    async def delete_column(self, user_id: int, column_id: int) -> bool:
        col = await self.get_column_if_owned(user_id, column_id)
        if not col:
            return False
        board_id = col.board_id
        await self._session.execute(delete(TodoColumnModel).where(TodoColumnModel.id == column_id))
        await self._session.flush()
        await self._compact_column_positions(board_id)
        return True

    async def _compact_column_positions(self, board_id: int) -> None:
        cols = await self._columns_for_board(board_id)
        now = _utc_now()
        for i, c in enumerate(cols):
            if c.position != i:
                c.position = i
                c.updated_at = now
                self._session.add(c)

    async def reorder_columns(self, user_id: int, ordered_column_ids: list[int]) -> bool:
        board = await self.get_board_row(user_id)
        if not board:
            return False
        cols = await self._columns_for_board(board.id)
        existing = {c.id for c in cols}
        if set(ordered_column_ids) != existing or len(ordered_column_ids) != len(existing):
            return False
        now = _utc_now()
        for i, cid in enumerate(ordered_column_ids):
            for c in cols:
                if c.id == cid and c.position != i:
                    c.position = i
                    c.updated_at = now
                    self._session.add(c)
        return True

    async def add_card(
        self,
        user_id: int,
        column_id: int,
        *,
        title: str,
        body: str | None,
        insert_at: int | None,
        due_at: datetime | None = None,
    ) -> TodoCardModel | None:
        col = await self.get_column_if_owned(user_id, column_id)
        if not col:
            return None
        cards = await self._cards_for_column(column_id)
        n = len(cards)
        pos = n if insert_at is None else max(0, min(int(insert_at), n))
        now = _utc_now()
        for c in cards:
            if c.position >= pos:
                c.position += 1
                c.updated_at = now
                self._session.add(c)
        card = TodoCardModel(
            column_id=column_id,
            title=title.strip(),
            body=body,
            position=pos,
            due_at=due_at,
            is_completed=False,
            is_archived=False,
            created_at=now,
            updated_at=None,
        )
        self._session.add(card)
        await self._session.flush()
        return card

    async def update_card(
        self,
        user_id: int,
        card_id: int,
        *,
        title: str | None,
        body: str | None,
        new_column_id: int | None,
        new_position: int | None,
        due_at: datetime | None = None,
        due_at_provided: bool = False,
        is_completed: bool | None = None,
        is_archived: bool | None = None,
    ) -> TodoCardModel | None:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return None
        now = _utc_now()
        if title is not None:
            card.title = title.strip()
        if body is not None:
            card.body = body
        if due_at_provided:
            card.due_at = due_at
        if is_completed is not None:
            card.is_completed = bool(is_completed)
        if is_archived is not None:
            card.is_archived = bool(is_archived)
        old_col = card.column_id
        if new_column_id is not None and new_column_id != old_col:
            tgt = await self.get_column_if_owned(user_id, new_column_id)
            if not tgt:
                return None
            old_pos = card.position
            for c in await self._cards_for_column_all(old_col):
                if c.id != card.id and c.position > old_pos:
                    c.position -= 1
                    c.updated_at = now
                    self._session.add(c)
            card.column_id = new_column_id
            await self._session.flush()
            others = [
                c
                for c in await self._cards_for_column(new_column_id)
                if c.id != card.id
            ]
            others.sort(key=lambda x: (x.position, x.id))
            np = (
                len(others)
                if new_position is None
                else max(0, min(int(new_position), len(others)))
            )
            for c in others:
                if c.position >= np:
                    c.position += 1
                    c.updated_at = now
                    self._session.add(c)
            card.position = np
        elif new_position is not None:
            col_id = card.column_id
            cards = await self._cards_for_column(col_id)
            ordered = sorted(cards, key=lambda x: (x.position, x.id))
            ordered_ids = [c.id for c in ordered]
            ordered_ids.remove(card.id)
            np = max(0, min(int(new_position), len(ordered_ids)))
            ordered_ids.insert(np, card.id)
            for i, cid in enumerate(ordered_ids):
                for c in cards:
                    if c.id == cid and c.position != i:
                        c.position = i
                        c.updated_at = now
                        self._session.add(c)
        card.updated_at = now
        self._session.add(card)
        return card

    async def delete_card(self, user_id: int, card_id: int) -> bool:
        card = await self.get_card_if_owned(user_id, card_id)
        if not card:
            return False
        col_id = card.column_id
        pos = card.position
        await self._session.execute(delete(TodoCardModel).where(TodoCardModel.id == card_id))
        await self._session.flush()
        cards = await self._cards_for_column_all(col_id)
        now = _utc_now()
        for c in cards:
            if c.position > pos:
                c.position -= 1
                c.updated_at = now
                self._session.add(c)
        return True

    async def reorder_cards_in_column(
        self,
        user_id: int,
        column_id: int,
        ordered_card_ids: list[int],
    ) -> bool:
        col = await self.get_column_if_owned(user_id, column_id)
        if not col:
            return False
        cards = await self._cards_for_column(column_id)
        existing = {c.id for c in cards}
        if set(ordered_card_ids) != existing or len(ordered_card_ids) != len(existing):
            return False
        now = _utc_now()
        for i, cid in enumerate(ordered_card_ids):
            for c in cards:
                if c.id == cid and c.position != i:
                    c.position = i
                    c.updated_at = now
                    self._session.add(c)
        return True
