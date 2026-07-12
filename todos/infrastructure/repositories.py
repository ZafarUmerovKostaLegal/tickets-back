from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from collections import defaultdict
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value
from application.ports import HealthRepositoryPort
from infrastructure.config import get_settings
from infrastructure.file_storage import save_todo_card_file
from infrastructure.models import (
    BOARD_VIS_SHARED,
    INVITE_ACCEPTED,
    INVITE_DECLINED,
    INVITE_PENDING,
    INVITE_REVOKED,
    OutlookCalendarTokenModel,
    TODO_BOARD_TITLE_PRIMARY_DEFAULT,
    TodoBoardInviteModel,
    TodoBoardLabelModel,
    TodoBoardMemberModel,
    TodoBoardModel,
    TodoCardAttachmentModel,
    TodoCardChecklistItemModel,
    TodoCardCommentModel,
    TodoCardLabelModel,
    TodoCardModel,
    TodoCardParticipantModel,
    TodoColumnModel,
    TodoUserPreferenceModel,
)
from infrastructure.token_crypto import (
    decrypt_token,
    encrypt_token,
    encryption_enabled,
    is_encrypted_token,
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
        row = r.scalars().one_or_none()
        if row is None:
            return None
        # Decrypt in-memory without marking the row dirty (avoids plaintext flush).
        set_committed_value(row, "access_token", decrypt_token(row.access_token))
        set_committed_value(row, "refresh_token", decrypt_token(row.refresh_token))
        return row

    async def upsert(
        self,
        *,
        user_id: int,
        access_token: str,
        refresh_token: str,
        expires_at: datetime | None,
    ) -> None:
        enc_access = encrypt_token(access_token)
        enc_refresh = encrypt_token(refresh_token)
        r = await self._session.execute(
            select(OutlookCalendarTokenModel).where(
                OutlookCalendarTokenModel.user_id == user_id
            )
        )
        row = r.scalars().one_or_none()
        if row:
            row.access_token = enc_access
            row.refresh_token = enc_refresh
            row.expires_at = expires_at
            self._session.add(row)
        else:
            self._session.add(
                OutlookCalendarTokenModel(
                    user_id=user_id,
                    access_token=enc_access,
                    refresh_token=enc_refresh,
                    expires_at=expires_at,
                )
            )

    async def reencrypt_plaintext_tokens(self) -> int:
        """Encrypt legacy plaintext rows when Fernet key is set. Idempotent; no data loss."""
        if not encryption_enabled():
            return 0
        r = await self._session.execute(select(OutlookCalendarTokenModel))
        rows = list(r.scalars().all())
        updated = 0
        for row in rows:
            changed = False
            if row.access_token and not is_encrypted_token(row.access_token):
                row.access_token = encrypt_token(row.access_token)
                changed = True
            if row.refresh_token and not is_encrypted_token(row.refresh_token):
                row.refresh_token = encrypt_token(row.refresh_token)
                changed = True
            if changed:
                self._session.add(row)
                updated += 1
        if updated:
            await self._session.commit()
        return updated


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_DEFAULT_KANBAN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Сегодня", "#7c3aed"),
    ("На этой неделе", "#2563eb"),
    ("Позже", "#ea580c"),
)

_INVITE_TTL_DAYS = 7


@dataclass
class AddBoardMembersResult:
    added_user_ids: list[int] = field(default_factory=list)
    invited: list[TodoBoardInviteModel] = field(default_factory=list)
    skipped_user_ids: list[int] = field(default_factory=list)


class KanbanRepository:


    def __init__(self, session: AsyncSession):
        self._session = session

    def add_default_kanban_columns(self, board_id: int, now: datetime) -> None:

        for i, (col_title, col_color) in enumerate(_DEFAULT_KANBAN_COLUMNS):
            self._session.add(
                TodoColumnModel(
                    board_id=board_id,
                    title=col_title,
                    position=i,
                    color=col_color,
                    is_collapsed=False,
                    created_at=now,
                    updated_at=None,
                )
            )

    async def get_board_by_id(self, board_id: int) -> TodoBoardModel | None:
        r = await self._session.execute(select(TodoBoardModel).where(TodoBoardModel.id == board_id))
        return r.scalars().one_or_none()

    async def board_role(self, user_id: int, board_id: int) -> str | None:
        b = await self.get_board_by_id(board_id)
        if not b or b.archived_at is not None:
            return None
        if b.user_id == user_id:
            return "owner"
        r = await self._session.execute(
            select(TodoBoardMemberModel).where(
                TodoBoardMemberModel.board_id == board_id,
                TodoBoardMemberModel.user_id == user_id,
            )
        )
        m = r.scalars().one_or_none()
        if m:
            return m.role
        rp = await self._session.execute(
            select(TodoCardParticipantModel)
            .join(TodoCardModel, TodoCardParticipantModel.card_id == TodoCardModel.id)
            .join(TodoColumnModel, TodoCardModel.column_id == TodoColumnModel.id)
            .where(
                TodoColumnModel.board_id == board_id,
                TodoCardParticipantModel.user_id == user_id,
            )
            .limit(1)
        )
        return "participant" if rp.scalars().one_or_none() else None

    async def require_board_read(self, user_id: int, board_id: int) -> TodoBoardModel | None:
        if await self.board_role(user_id, board_id) is None:
            return None
        return await self.get_board_by_id(board_id)

    async def require_board_write(self, user_id: int, board_id: int) -> TodoBoardModel | None:
        role = await self.board_role(user_id, board_id)
        if role not in ("owner", "editor"):
            return None
        return await self.get_board_by_id(board_id)

    async def get_primary_owned_board(self, user_id: int) -> TodoBoardModel | None:
        r = await self._session.execute(
            select(TodoBoardModel)
            .where(
                TodoBoardModel.user_id == user_id,
                TodoBoardModel.archived_at.is_(None),
            )
            .order_by(TodoBoardModel.sort_order.asc(), TodoBoardModel.id.asc())
            .limit(1)
        )
        return r.scalars().one_or_none()

    async def get_last_selected_board_id(self, user_id: int) -> int | None:
        r = await self._session.execute(
            select(TodoUserPreferenceModel.last_selected_board_id).where(
                TodoUserPreferenceModel.user_id == user_id
            )
        )
        board_id = r.scalar_one_or_none()
        if board_id is None:
            return None
        if await self.board_role(user_id, int(board_id)) is None:
            await self.set_last_selected_board_id(user_id, None)
            return None
        return int(board_id)

    async def get_last_selected_board(self, user_id: int) -> TodoBoardModel | None:
        board_id = await self.get_last_selected_board_id(user_id)
        if board_id is None:
            return None
        return await self.get_board_by_id(board_id)

    async def set_last_selected_board_id(
        self,
        user_id: int,
        board_id: int | None,
    ) -> TodoUserPreferenceModel:
        if board_id is not None and await self.board_role(user_id, int(board_id)) is None:
            raise ValueError("board is not accessible")

        now = _utc_now()
        r = await self._session.execute(
            select(TodoUserPreferenceModel).where(TodoUserPreferenceModel.user_id == user_id)
        )
        pref = r.scalars().one_or_none()
        if pref is None:
            pref = TodoUserPreferenceModel(
                user_id=user_id,
                last_selected_board_id=board_id,
                updated_at=now,
            )
        else:
            pref.last_selected_board_id = board_id
            pref.updated_at = now
        self._session.add(pref)
        await self._session.flush()
        return pref

    async def get_board_row(self, user_id: int) -> TodoBoardModel | None:
        return await self.get_primary_owned_board(user_id)

    async def ensure_board(self, user_id: int) -> TodoBoardModel:
        row = await self.get_primary_owned_board(user_id)
        if row:
            return row
        now = _utc_now()
        row = TodoBoardModel(
            user_id=user_id,
            title=TODO_BOARD_TITLE_PRIMARY_DEFAULT,
            visibility="personal",
            color=None,
            sort_order=0,
            archived_at=None,
            background_url=None,
            created_at=now,
            updated_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        self.add_default_kanban_columns(row.id, now)
        return row

    async def list_board_labels_for_board(self, board_id: int) -> list[TodoBoardLabelModel]:
        r = await self._session.execute(
            select(TodoBoardLabelModel).where(TodoBoardLabelModel.board_id == board_id)
        )
        rows = list(r.scalars().all())
        rows.sort(key=lambda x: (x.position, x.id))
        return rows

    async def list_accessible_boards_with_roles(
        self,
        user_id: int,
    ) -> list[tuple[TodoBoardModel, str]]:

        r_own = await self._session.execute(
            select(TodoBoardModel).where(
                TodoBoardModel.user_id == user_id,
                TodoBoardModel.archived_at.is_(None),
            )
        )
        owned = list(r_own.scalars().all())
        out: list[tuple[TodoBoardModel, str]] = [(b, "owner") for b in owned]
        seen = {b.id for b, _ in out}
        r_mem = await self._session.execute(
            select(TodoBoardModel, TodoBoardMemberModel.role)
            .join(
                TodoBoardMemberModel,
                TodoBoardMemberModel.board_id == TodoBoardModel.id,
            )
            .where(
                TodoBoardMemberModel.user_id == user_id,
                TodoBoardModel.archived_at.is_(None),
            )
        )
        for board, role in r_mem.all():
            if board.id not in seen:
                out.append((board, str(role)))
                seen.add(board.id)
        r_part = await self._session.execute(
            select(TodoBoardModel)
            .join(TodoColumnModel, TodoColumnModel.board_id == TodoBoardModel.id)
            .join(TodoCardModel, TodoCardModel.column_id == TodoColumnModel.id)
            .join(TodoCardParticipantModel, TodoCardParticipantModel.card_id == TodoCardModel.id)
            .where(
                TodoCardParticipantModel.user_id == user_id,
                TodoBoardModel.archived_at.is_(None),
            )
        )
        for board in r_part.scalars().all():
            if board.id not in seen:
                out.append((board, "participant"))
                seen.add(board.id)
        out.sort(key=lambda t: (t[0].sort_order, t[0].id))
        return out

    async def create_board(
        self,
        owner_user_id: int,
        *,
        title: str,
        visibility: str,
        color: str | None,
        member_user_ids: list[int],
        instant_add_members: bool,
    ) -> TodoBoardModel:
        now = _utc_now()
        rmax = await self._session.execute(
            select(TodoBoardModel.sort_order).where(
                TodoBoardModel.user_id == owner_user_id,
                TodoBoardModel.archived_at.is_(None),
            )
        )
        orders = [x[0] for x in rmax.all()]
        next_order = (max(orders) + 1) if orders else 0
        board = TodoBoardModel(
            user_id=owner_user_id,
            title=title.strip()[:200],
            visibility=visibility,
            color=(color.strip()[:32] if color else None),
            sort_order=next_order,
            archived_at=None,
            background_url=None,
            created_at=now,
            updated_at=None,
        )
        self._session.add(board)
        await self._session.flush()
        self.add_default_kanban_columns(board.id, now)
        uniq_members = sorted({int(x) for x in member_user_ids if int(x) != owner_user_id})
        if visibility == BOARD_VIS_SHARED and uniq_members:
            if instant_add_members:
                for uid in uniq_members:
                    self._session.add(
                        TodoBoardMemberModel(
                            board_id=board.id,
                            user_id=uid,
                            role="editor",
                            joined_at=now,
                        )
                    )
            else:
                for uid in uniq_members:
                    self._session.add(
                        TodoBoardInviteModel(
                            board_id=board.id,
                            inviter_user_id=owner_user_id,
                            invitee_user_id=uid,
                            role_offered="editor",
                            status=INVITE_PENDING,
                            message=None,
                            created_at=now,
                            expires_at=now + timedelta(days=_INVITE_TTL_DAYS),
                            resolved_at=None,
                        )
                    )
        await self._session.flush()
        return board

    async def patch_board_meta(
        self,
        user_id: int,
        board_id: int,
        *,
        title: str | None,
        color: str | None,
        visibility: str | None,
        background_url: str | None,
        background_url_set: bool,
    ) -> TodoBoardModel | None:
        b = await self.require_board_write(user_id, board_id)
        if not b:
            return None
        now = _utc_now()
        if title is not None:
            b.title = title.strip()[:200]
        if color is not None:
            b.color = color.strip()[:32] if color else None
        if visibility is not None:
            b.visibility = visibility
        if background_url_set:
            b.background_url = background_url
        b.updated_at = now
        self._session.add(b)
        return b

    async def archive_board(self, user_id: int, board_id: int) -> bool:
        b = await self.get_board_by_id(board_id)
        if not b or b.archived_at is not None:
            return False
        if b.user_id != user_id:
            return False
        now = _utc_now()
        b.archived_at = now
        b.updated_at = now
        self._session.add(b)
        return True

    async def list_pending_invites_for_user(
        self,
        user_id: int,
    ) -> list[tuple[TodoBoardInviteModel, TodoBoardModel]]:
        now = _utc_now()
        r = await self._session.execute(
            select(TodoBoardInviteModel, TodoBoardModel)
            .join(TodoBoardModel, TodoBoardModel.id == TodoBoardInviteModel.board_id)
            .where(
                TodoBoardInviteModel.invitee_user_id == user_id,
                TodoBoardInviteModel.status == INVITE_PENDING,
                TodoBoardInviteModel.expires_at > now,
                TodoBoardModel.archived_at.is_(None),
            )
            .order_by(TodoBoardInviteModel.created_at.desc())
        )
        return list(r.all())

    async def list_pending_invites_for_board(
        self,
        user_id: int,
        board_id: int,
    ) -> list[TodoBoardInviteModel]:
        if await self.require_board_write(user_id, board_id) is None:
            return []
        r = await self._session.execute(
            select(TodoBoardInviteModel).where(
                TodoBoardInviteModel.board_id == board_id,
                TodoBoardInviteModel.status == INVITE_PENDING,
            )
        )
        return list(r.scalars().all())

    async def create_board_invites(
        self,
        actor_user_id: int,
        board_id: int,
        *,
        invitee_ids: list[int],
        role: str,
        message: str | None,
    ) -> list[TodoBoardInviteModel] | None:
        b = await self.require_board_write(actor_user_id, board_id)
        if not b:
            return None
        now = _utc_now()
        created: list[TodoBoardInviteModel] = []
        board_owner = b.user_id
        for raw in sorted({int(x) for x in invitee_ids}):
            if raw == board_owner:
                continue
            rm = await self._session.execute(
                select(TodoBoardMemberModel).where(
                    TodoBoardMemberModel.board_id == board_id,
                    TodoBoardMemberModel.user_id == raw,
                )
            )
            if rm.scalars().one_or_none():
                continue
            rp = await self._session.execute(
                select(TodoBoardInviteModel).where(
                    TodoBoardInviteModel.board_id == board_id,
                    TodoBoardInviteModel.invitee_user_id == raw,
                    TodoBoardInviteModel.status == INVITE_PENDING,
                )
            )
            if rp.scalars().one_or_none():
                continue
            inv = TodoBoardInviteModel(
                board_id=board_id,
                inviter_user_id=actor_user_id,
                invitee_user_id=raw,
                role_offered=role,
                status=INVITE_PENDING,
                message=(message[:500] if message else None),
                created_at=now,
                expires_at=now + timedelta(days=_INVITE_TTL_DAYS),
                resolved_at=None,
            )
            self._session.add(inv)
            created.append(inv)
        await self._session.flush()
        return created

    async def add_board_members(
        self,
        actor_user_id: int,
        board_id: int,
        *,
        user_ids: list[int],
        role: str,
        instant: bool,
    ) -> AddBoardMembersResult | None:
        b = await self.require_board_write(actor_user_id, board_id)
        if not b or b.archived_at is not None or b.visibility != BOARD_VIS_SHARED:
            return None
        now = _utc_now()
        result = AddBoardMembersResult()
        board_owner = b.user_id
        for raw in sorted({int(x) for x in user_ids}):
            if raw == board_owner:
                result.skipped_user_ids.append(raw)
                continue
            rm = await self._session.execute(
                select(TodoBoardMemberModel).where(
                    TodoBoardMemberModel.board_id == board_id,
                    TodoBoardMemberModel.user_id == raw,
                )
            )
            if rm.scalars().one_or_none():
                result.skipped_user_ids.append(raw)
                continue
            if instant:
                self._session.add(
                    TodoBoardMemberModel(
                        board_id=board_id,
                        user_id=raw,
                        role=role,
                        joined_at=now,
                    )
                )
                result.added_user_ids.append(raw)
            else:
                rp = await self._session.execute(
                    select(TodoBoardInviteModel).where(
                        TodoBoardInviteModel.board_id == board_id,
                        TodoBoardInviteModel.invitee_user_id == raw,
                        TodoBoardInviteModel.status == INVITE_PENDING,
                    )
                )
                if rp.scalars().one_or_none():
                    result.skipped_user_ids.append(raw)
                    continue
                inv = TodoBoardInviteModel(
                    board_id=board_id,
                    inviter_user_id=actor_user_id,
                    invitee_user_id=raw,
                    role_offered=role,
                    status=INVITE_PENDING,
                    message=None,
                    created_at=now,
                    expires_at=now + timedelta(days=_INVITE_TTL_DAYS),
                    resolved_at=None,
                )
                self._session.add(inv)
                result.invited.append(inv)
        await self._session.flush()
        return result

    async def accept_invite(self, invite_id: int, user_id: int) -> TodoBoardInviteModel | None:
        now = _utc_now()
        r = await self._session.execute(
            select(TodoBoardInviteModel).where(TodoBoardInviteModel.id == invite_id)
        )
        inv = r.scalars().one_or_none()
        if (
            not inv
            or inv.invitee_user_id != user_id
            or inv.status != INVITE_PENDING
            or inv.expires_at <= now
        ):
            return None
        rb = await self._session.execute(
            select(TodoBoardMemberModel).where(
                TodoBoardMemberModel.board_id == inv.board_id,
                TodoBoardMemberModel.user_id == user_id,
            )
        )
        if rb.scalars().one_or_none():
            inv.status = INVITE_ACCEPTED
            inv.resolved_at = now
            self._session.add(inv)
            await self._session.flush()
            return inv
        inv.status = INVITE_ACCEPTED
        inv.resolved_at = now
        self._session.add(inv)
        self._session.add(
            TodoBoardMemberModel(
                board_id=inv.board_id,
                user_id=user_id,
                role=inv.role_offered or "editor",
                joined_at=now,
            )
        )
        await self._session.flush()
        return inv

    async def decline_invite(self, invite_id: int, user_id: int) -> bool:
        now = _utc_now()
        r = await self._session.execute(
            select(TodoBoardInviteModel).where(TodoBoardInviteModel.id == invite_id)
        )
        inv = r.scalars().one_or_none()
        if not inv or inv.invitee_user_id != user_id or inv.status != INVITE_PENDING:
            return False
        inv.status = INVITE_DECLINED
        inv.resolved_at = now
        self._session.add(inv)
        return True

    async def revoke_invite(self, invite_id: int, actor_user_id: int) -> bool:
        now = _utc_now()
        r = await self._session.execute(
            select(TodoBoardInviteModel).where(TodoBoardInviteModel.id == invite_id)
        )
        inv = r.scalars().one_or_none()
        if not inv or inv.status != INVITE_PENDING:
            return False
        b = await self.get_board_by_id(inv.board_id)
        if not b:
            return False
        if b.user_id != actor_user_id and inv.inviter_user_id != actor_user_id:
            return False
        inv.status = INVITE_REVOKED
        inv.resolved_at = now
        self._session.add(inv)
        return True

    async def list_board_members(
        self,
        user_id: int,
        board_id: int,
    ) -> list[TodoBoardMemberModel] | None:
        if await self.require_board_read(user_id, board_id) is None:
            return None
        r = await self._session.execute(
            select(TodoBoardMemberModel).where(TodoBoardMemberModel.board_id == board_id)
        )
        rows = list(r.scalars().all())
        rows.sort(key=lambda m: (m.user_id,))
        return rows

    async def remove_board_member(
        self,
        actor_user_id: int,
        board_id: int,
        member_user_id: int,
    ) -> bool:
        b = await self.get_board_by_id(board_id)
        if not b or b.archived_at is not None:
            return False
        if b.user_id != actor_user_id:
            return False
        if member_user_id == b.user_id:
            return False
        r = await self._session.execute(
            delete(TodoBoardMemberModel).where(
                TodoBoardMemberModel.board_id == board_id,
                TodoBoardMemberModel.user_id == member_user_id,
            )
        )
        return r.rowcount > 0                                          

    async def patch_board_member_role(
        self,
        actor_user_id: int,
        board_id: int,
        member_user_id: int,
        *,
        role: str,
    ) -> TodoBoardMemberModel | None:
        b = await self.get_board_by_id(board_id)
        if not b or b.archived_at is not None or b.user_id != actor_user_id:
            return None
        if member_user_id == b.user_id:
            return None
        r = await self._session.execute(
            select(TodoBoardMemberModel).where(
                TodoBoardMemberModel.board_id == board_id,
                TodoBoardMemberModel.user_id == member_user_id,
            )
        )
        row = r.scalars().one_or_none()
        if not row:
            return None
        row.role = role
        self._session.add(row)
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
        *,
        need_write: bool = True,
    ) -> TodoColumnModel | None:
        r = await self._session.execute(
            select(TodoColumnModel).where(TodoColumnModel.id == column_id)
        )
        col = r.scalars().one_or_none()
        if not col:
            return None
        role = await self.board_role(user_id, col.board_id)
        if role is None:
            return None
        if need_write and role == "viewer":
            return None
        return col

    async def get_card_if_owned(
        self,
        user_id: int,
        card_id: int,
        *,
        need_write: bool = True,
    ) -> TodoCardModel | None:
        r = await self._session.execute(
            select(TodoCardModel, TodoColumnModel)
            .join(TodoColumnModel, TodoCardModel.column_id == TodoColumnModel.id)
            .where(TodoCardModel.id == card_id)
        )
        row = r.one_or_none()
        if not row:
            return None
        card, col = row[0], row[1]
        role = await self.board_role(user_id, col.board_id)
        if role is None:
            return None
        if need_write and role == "viewer":
            return None
        return card

    async def patch_board(
        self,
        user_id: int,
        *,
        background_url: str | None,
    ) -> TodoBoardModel:
        row = await self.get_last_selected_board(user_id)
        if row is None:
            row = await self.ensure_board(user_id)
        row.background_url = background_url
        row.updated_at = _utc_now()
        self._session.add(row)
        return row

    async def add_column(
        self,
        user_id: int,
        *,
        board_id: int | None = None,
        title: str,
        color: str,
        insert_at: int | None,
        is_collapsed: bool = False,
    ) -> TodoColumnModel | None:
        if board_id is None:
            board = await self.ensure_board(user_id)
            bid = board.id
        else:
            b = await self.require_board_write(user_id, board_id)
            if not b:
                return None
            bid = board_id
        cols = await self._columns_for_board(bid)
        n = len(cols)
        pos = n if insert_at is None else max(0, min(int(insert_at), n))
        now = _utc_now()
        for c in cols:
            if c.position >= pos:
                c.position += 1
                c.updated_at = now
                self._session.add(c)
        col = TodoColumnModel(
            board_id=bid,
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
        return await self.list_board_labels_for_board(board.id)

    async def add_board_label(
        self,
        user_id: int,
        *,
        title: str,
        color: str,
        board_id: int | None = None,
    ) -> TodoBoardLabelModel | None:
        if board_id is None:
            board = await self.ensure_board(user_id)
            bid = board.id
        else:
            b = await self.require_board_write(user_id, board_id)
            if not b:
                return None
            bid = board_id
        existing = await self.list_board_labels_for_board(bid)
        n = len(existing)
        now = _utc_now()
        row = TodoBoardLabelModel(
            board_id=bid,
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
        r = await self._session.execute(
            select(TodoBoardLabelModel).where(TodoBoardLabelModel.id == label_id)
        )
        row = r.scalars().one_or_none()
        if not row:
            return None
        if await self.require_board_write(user_id, row.board_id) is None:
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
        r = await self._session.execute(
            select(TodoBoardLabelModel).where(TodoBoardLabelModel.id == label_id)
        )
        row = r.scalars().one_or_none()
        if not row:
            return False
        if await self.require_board_write(user_id, row.board_id) is None:
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
        from backend_common.media_path import safe_media_path

        p = safe_media_path(get_settings().media_path, row.storage_key)
        if p is not None and p.is_file():
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
        if not ordered_column_ids:
            return False
        r0 = await self._session.execute(
            select(TodoColumnModel).where(TodoColumnModel.id == ordered_column_ids[0])
        )
        col0 = r0.scalars().one_or_none()
        if not col0:
            return False
        bid = col0.board_id
        if await self.require_board_write(user_id, bid) is None:
            return False
        cols = await self._columns_for_board(bid)
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
