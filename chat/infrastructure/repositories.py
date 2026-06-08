from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import (
    KOSTA_DAILY_SLUG,
    MEMBER_ROLE_ADMIN,
    MEMBER_ROLE_MEMBER,
    MESSAGE_KIND_POLL,
    MESSAGE_KIND_QUIZ,
    POLL_KIND_QUIZ,
    REACTION_MAX_EMOJI_LEN,
    ROOM_TYPE_CHANNEL,
    ROOM_TYPE_COMPANY,
    ROOM_TYPE_DM,
    ROOM_TYPE_GROUP,
    ChatMessageAttachmentModel,
    ChatMessageModel,
    ChatMessageReactionModel,
    ChatPollModel,
    ChatPollVoteModel,
    ChatReadStateModel,
    ChatRoomMemberModel,
    ChatRoomModel,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RoomListRow:
    room: ChatRoomModel
    member_role: str
    last_message: ChatMessageModel | None
    unread_count: int


class ChatRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ensure_company_room(self) -> ChatRoomModel:
        r = await self._session.execute(
            select(ChatRoomModel).where(ChatRoomModel.slug == KOSTA_DAILY_SLUG)
        )
        room = r.scalars().one_or_none()
        if room:
            return room
        now = _utc_now()
        room = ChatRoomModel(
            slug=KOSTA_DAILY_SLUG,
            title="Kosta Daily",
            room_type=ROOM_TYPE_COMPANY,
            created_by_user_id=None,
            dm_user_low=None,
            dm_user_high=None,
            created_at=now,
        )
        self._session.add(room)
        await self._session.flush()
        return room

    async def ensure_company_membership(self, user_id: int) -> None:
        room = await self.ensure_company_room()
        r = await self._session.execute(
            select(ChatRoomMemberModel).where(
                ChatRoomMemberModel.room_id == room.id,
                ChatRoomMemberModel.user_id == user_id,
            )
        )
        if r.scalars().one_or_none():
            return
        self._session.add(
            ChatRoomMemberModel(
                room_id=room.id,
                user_id=user_id,
                role=MEMBER_ROLE_MEMBER,
                joined_at=_utc_now(),
            )
        )
        await self._session.flush()

    async def is_member(self, user_id: int, room_id: int) -> ChatRoomMemberModel | None:
        r = await self._session.execute(
            select(ChatRoomMemberModel).where(
                ChatRoomMemberModel.room_id == room_id,
                ChatRoomMemberModel.user_id == user_id,
            )
        )
        return r.scalars().one_or_none()

    async def get_room(self, room_id: int) -> ChatRoomModel | None:
        r = await self._session.execute(select(ChatRoomModel).where(ChatRoomModel.id == room_id))
        return r.scalars().one_or_none()

    async def list_rooms_for_user(self, user_id: int) -> list[RoomListRow]:
        await self.ensure_company_membership(user_id)
        r = await self._session.execute(
            select(ChatRoomModel, ChatRoomMemberModel.role)
            .join(ChatRoomMemberModel, ChatRoomMemberModel.room_id == ChatRoomModel.id)
            .where(ChatRoomMemberModel.user_id == user_id)
            .order_by(ChatRoomModel.room_type.asc(), ChatRoomModel.id.asc())
        )
        rows: list[RoomListRow] = []
        for room, role in r.all():
            last_msg = await self._last_message(room.id)
            unread = await self._unread_count(user_id, room.id, last_msg.id if last_msg else None)
            rows.append(
                RoomListRow(room=room, member_role=str(role), last_message=last_msg, unread_count=unread)
            )
        def _sort_key(x: RoomListRow) -> tuple:
            if x.room.slug == KOSTA_DAILY_SLUG:
                bucket = 0
            elif x.room.room_type in (ROOM_TYPE_COMPANY, ROOM_TYPE_CHANNEL):
                bucket = 1
            elif x.room.room_type == ROOM_TYPE_GROUP:
                bucket = 2
            else:
                bucket = 3
            ts = -(x.last_message.created_at.timestamp() if x.last_message else 0)
            return (bucket, ts, x.room.id)

        rows.sort(key=_sort_key)
        return rows

    async def can_user_post(self, user_id: int, room_id: int) -> bool:
        member = await self.is_member(user_id, room_id)
        if not member:
            return False
        room = await self.get_room(room_id)
        if not room:
            return False
        if room.room_type in (ROOM_TYPE_COMPANY, ROOM_TYPE_CHANNEL):
            return member.role == MEMBER_ROLE_ADMIN
        return True

    async def _last_message(self, room_id: int) -> ChatMessageModel | None:
        r = await self._session.execute(
            select(ChatMessageModel)
            .where(
                ChatMessageModel.room_id == room_id,
                ChatMessageModel.deleted_at.is_(None),
            )
            .order_by(ChatMessageModel.id.desc())
            .limit(1)
        )
        return r.scalars().one_or_none()

    async def _unread_count(
        self, user_id: int, room_id: int, last_visible_id: int | None
    ) -> int:
        rs = await self._session.execute(
            select(ChatReadStateModel).where(
                ChatReadStateModel.room_id == room_id,
                ChatReadStateModel.user_id == user_id,
            )
        )
        state = rs.scalars().one_or_none()
        after_id = state.last_read_message_id if state else None
        q = select(func.count()).select_from(ChatMessageModel).where(
            ChatMessageModel.room_id == room_id,
            ChatMessageModel.deleted_at.is_(None),
            ChatMessageModel.author_user_id != user_id,
        )
        if after_id is not None:
            q = q.where(ChatMessageModel.id > after_id)
        r = await self._session.execute(q)
        return int(r.scalar() or 0)

    async def list_messages(
        self,
        user_id: int,
        room_id: int,
        *,
        before_id: int | None,
        limit: int,
    ) -> list[ChatMessageModel] | None:
        if await self.is_member(user_id, room_id) is None:
            return None
        q = (
            select(ChatMessageModel)
            .where(ChatMessageModel.room_id == room_id)
            .order_by(ChatMessageModel.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            q = q.where(ChatMessageModel.id < before_id)
        r = await self._session.execute(q)
        items = list(r.scalars().all())
        items.reverse()
        return items

    async def get_message_in_room(self, message_id: int, room_id: int) -> ChatMessageModel | None:
        r = await self._session.execute(
            select(ChatMessageModel).where(
                ChatMessageModel.id == message_id,
                ChatMessageModel.room_id == room_id,
            )
        )
        return r.scalars().one_or_none()

    async def messages_by_ids(self, message_ids: list[int]) -> dict[int, ChatMessageModel]:
        if not message_ids:
            return {}
        r = await self._session.execute(
            select(ChatMessageModel).where(ChatMessageModel.id.in_(message_ids))
        )
        return {m.id: m for m in r.scalars().all()}

    async def create_message(
        self,
        user_id: int,
        room_id: int,
        body: str,
        *,
        reply_to_message_id: int | None = None,
        message_kind: str = "text",
    ) -> ChatMessageModel | None:
        if await self.is_member(user_id, room_id) is None:
            return None
        if not await self.can_user_post(user_id, room_id):
            return None
        if reply_to_message_id is not None:
            parent = await self.get_message_in_room(reply_to_message_id, room_id)
            if parent is None:
                return None
        now = _utc_now()
        msg = ChatMessageModel(
            room_id=room_id,
            author_user_id=user_id,
            message_kind=message_kind,
            body=body,
            reply_to_message_id=reply_to_message_id,
            created_at=now,
            edited_at=None,
            deleted_at=None,
        )
        self._session.add(msg)
        await self._session.flush()
        await self._touch_read_state(user_id, room_id, msg.id, now)
        return msg

    async def add_message_attachment(
        self,
        *,
        message_id: int,
        file_name: str,
        content_type: str,
        size_bytes: int,
        storage_key: str,
    ) -> ChatMessageAttachmentModel:
        att = ChatMessageAttachmentModel(
            message_id=message_id,
            file_name=file_name[:255],
            content_type=(content_type or "application/octet-stream")[:128],
            size_bytes=int(size_bytes),
            storage_key=storage_key,
            created_at=_utc_now(),
        )
        self._session.add(att)
        await self._session.flush()
        return att

    async def attachments_for_message_ids(
        self, message_ids: list[int]
    ) -> dict[int, list[ChatMessageAttachmentModel]]:
        out: dict[int, list[ChatMessageAttachmentModel]] = {}
        if not message_ids:
            return out
        r = await self._session.execute(
            select(ChatMessageAttachmentModel)
            .where(ChatMessageAttachmentModel.message_id.in_(message_ids))
            .order_by(ChatMessageAttachmentModel.id.asc())
        )
        for att in r.scalars().all():
            out.setdefault(att.message_id, []).append(att)
        return out

    async def get_attachment_with_room(
        self, attachment_id: int
    ) -> tuple[ChatMessageAttachmentModel, int] | None:
        r = await self._session.execute(
            select(ChatMessageAttachmentModel, ChatMessageModel.room_id)
            .join(ChatMessageModel, ChatMessageModel.id == ChatMessageAttachmentModel.message_id)
            .where(ChatMessageAttachmentModel.id == attachment_id)
        )
        row = r.one_or_none()
        if not row:
            return None
        att, room_id = row
        return att, int(room_id)

    async def edit_message(
        self,
        user_id: int,
        message_id: int,
        body: str,
    ) -> ChatMessageModel | None:
        r = await self._session.execute(
            select(ChatMessageModel).where(ChatMessageModel.id == message_id)
        )
        msg = r.scalars().one_or_none()
        if not msg or msg.deleted_at is not None or msg.author_user_id != user_id:
            return None
        if await self.is_member(user_id, msg.room_id) is None:
            return None
        now = _utc_now()
        msg.body = body
        msg.edited_at = now
        self._session.add(msg)
        return msg

    async def soft_delete_message(self, user_id: int, message_id: int) -> ChatMessageModel | None:
        r = await self._session.execute(
            select(ChatMessageModel).where(ChatMessageModel.id == message_id)
        )
        msg = r.scalars().one_or_none()
        if not msg or msg.deleted_at is not None:
            return None
        member = await self.is_member(user_id, msg.room_id)
        if not member:
            return None
        if msg.author_user_id != user_id and member.role != MEMBER_ROLE_ADMIN:
            return None
        msg.deleted_at = _utc_now()
        self._session.add(msg)
        return msg

    async def mark_read(
        self,
        user_id: int,
        room_id: int,
        message_id: int | None,
    ) -> bool:
        if await self.is_member(user_id, room_id) is None:
            return False
        if message_id is None:
            last = await self._last_message(room_id)
            message_id = last.id if last else None
        if message_id is None:
            return True
        await self._touch_read_state(user_id, room_id, message_id, _utc_now())
        return True

    async def _touch_read_state(
        self, user_id: int, room_id: int, message_id: int, now: datetime
    ) -> None:
        r = await self._session.execute(
            select(ChatReadStateModel).where(
                ChatReadStateModel.room_id == room_id,
                ChatReadStateModel.user_id == user_id,
            )
        )
        row = r.scalars().one_or_none()
        if row:
            if row.last_read_message_id is None or message_id > row.last_read_message_id:
                row.last_read_message_id = message_id
                row.updated_at = now
                self._session.add(row)
        else:
            self._session.add(
                ChatReadStateModel(
                    room_id=room_id,
                    user_id=user_id,
                    last_read_message_id=message_id,
                    updated_at=now,
                )
            )

    async def list_members(self, user_id: int, room_id: int) -> list[ChatRoomMemberModel] | None:
        if await self.is_member(user_id, room_id) is None:
            return None
        r = await self._session.execute(
            select(ChatRoomMemberModel)
            .where(ChatRoomMemberModel.room_id == room_id)
            .order_by(ChatRoomMemberModel.user_id.asc())
        )
        return list(r.scalars().all())

    async def member_user_ids(self, room_id: int) -> list[int]:
        r = await self._session.execute(
            select(ChatRoomMemberModel.user_id).where(ChatRoomMemberModel.room_id == room_id)
        )
        return [int(x[0]) for x in r.all()]

    async def _create_multi_user_room(
        self,
        creator_id: int,
        *,
        title: str,
        room_type: str,
        member_user_ids: list[int],
    ) -> ChatRoomModel:
        now = _utc_now()
        uniq = sorted({int(x) for x in member_user_ids if int(x) != creator_id})
        room = ChatRoomModel(
            slug=None,
            title=title.strip()[:200],
            room_type=room_type,
            created_by_user_id=creator_id,
            dm_user_low=None,
            dm_user_high=None,
            created_at=now,
        )
        self._session.add(room)
        await self._session.flush()
        members = {creator_id, *uniq}
        for uid in sorted(members):
            self._session.add(
                ChatRoomMemberModel(
                    room_id=room.id,
                    user_id=uid,
                    role=MEMBER_ROLE_ADMIN if uid == creator_id else MEMBER_ROLE_MEMBER,
                    joined_at=now,
                )
            )
        await self._session.flush()
        return room

    async def create_group_room(
        self,
        creator_id: int,
        *,
        title: str,
        member_user_ids: list[int],
    ) -> ChatRoomModel:
        return await self._create_multi_user_room(
            creator_id,
            title=title,
            room_type=ROOM_TYPE_GROUP,
            member_user_ids=member_user_ids,
        )

    async def create_channel_room(
        self,
        creator_id: int,
        *,
        title: str,
        member_user_ids: list[int],
    ) -> ChatRoomModel:
        return await self._create_multi_user_room(
            creator_id,
            title=title,
            room_type=ROOM_TYPE_CHANNEL,
            member_user_ids=member_user_ids,
        )

    async def get_or_create_dm_room(self, user_id: int, other_user_id: int) -> ChatRoomModel | None:
        if other_user_id == user_id:
            return None
        low, high = (user_id, other_user_id) if user_id < other_user_id else (other_user_id, user_id)
        r = await self._session.execute(
            select(ChatRoomModel).where(
                ChatRoomModel.room_type == ROOM_TYPE_DM,
                ChatRoomModel.dm_user_low == low,
                ChatRoomModel.dm_user_high == high,
            )
        )
        room = r.scalars().one_or_none()
        if room:
            return room
        now = _utc_now()
        room = ChatRoomModel(
            slug=None,
            title=f"DM:{low}:{high}",
            room_type=ROOM_TYPE_DM,
            created_by_user_id=user_id,
            dm_user_low=low,
            dm_user_high=high,
            created_at=now,
        )
        self._session.add(room)
        await self._session.flush()
        for uid in (low, high):
            self._session.add(
                ChatRoomMemberModel(
                    room_id=room.id,
                    user_id=uid,
                    role=MEMBER_ROLE_MEMBER,
                    joined_at=now,
                )
            )
        await self._session.flush()
        return room

    async def add_group_members(
        self,
        actor_id: int,
        room_id: int,
        user_ids: list[int],
    ) -> list[int] | None:
        room = await self.get_room(room_id)
        member = await self.is_member(actor_id, room_id)
        if not room or not member or room.room_type not in (ROOM_TYPE_GROUP, ROOM_TYPE_CHANNEL):
            return None
        if member.role != MEMBER_ROLE_ADMIN and room.created_by_user_id != actor_id:
            return None
        now = _utc_now()
        added: list[int] = []
        for raw in sorted({int(x) for x in user_ids}):
            if await self.is_member(raw, room_id):
                continue
            self._session.add(
                ChatRoomMemberModel(
                    room_id=room_id,
                    user_id=raw,
                    role=MEMBER_ROLE_MEMBER,
                    joined_at=now,
                )
            )
            added.append(raw)
        await self._session.flush()
        return added


    async def reactions_for_message_ids(
        self, message_ids: list[int]
    ) -> dict[int, list[ChatMessageReactionModel]]:
        out: dict[int, list[ChatMessageReactionModel]] = {}
        if not message_ids:
            return out
        r = await self._session.execute(
            select(ChatMessageReactionModel)
            .where(ChatMessageReactionModel.message_id.in_(message_ids))
            .order_by(ChatMessageReactionModel.message_id, ChatMessageReactionModel.emoji, ChatMessageReactionModel.id)
        )
        for rx in r.scalars().all():
            out.setdefault(rx.message_id, []).append(rx)
        return out

    async def toggle_reaction(
        self,
        user_id: int,
        message_id: int,
        emoji: str,
    ) -> list[ChatMessageReactionModel] | None:
        """Toggle a reaction. Returns updated list of reactions for the message, or None if not allowed."""
        emoji = emoji.strip()[:REACTION_MAX_EMOJI_LEN]
        if not emoji:
            return None
        r = await self._session.execute(
            select(ChatMessageModel).where(ChatMessageModel.id == message_id)
        )
        msg = r.scalars().one_or_none()
        if not msg:
            return None
        if await self.is_member(user_id, msg.room_id) is None:
            return None
        existing_r = await self._session.execute(
            select(ChatMessageReactionModel).where(
                ChatMessageReactionModel.message_id == message_id,
                ChatMessageReactionModel.user_id == user_id,
                ChatMessageReactionModel.emoji == emoji,
            )
        )
        existing = existing_r.scalars().one_or_none()
        if existing:
            await self._session.delete(existing)
        else:
            self._session.add(
                ChatMessageReactionModel(
                    message_id=message_id,
                    user_id=user_id,
                    emoji=emoji,
                    created_at=_utc_now(),
                )
            )
        await self._session.flush()
        updated = await self._session.execute(
            select(ChatMessageReactionModel)
            .where(ChatMessageReactionModel.message_id == message_id)
            .order_by(ChatMessageReactionModel.emoji, ChatMessageReactionModel.id)
        )
        return list(updated.scalars().all())

    async def polls_for_message_ids(self, message_ids: list[int]) -> dict[int, ChatPollModel]:
        out: dict[int, ChatPollModel] = {}
        if not message_ids:
            return out
        r = await self._session.execute(
            select(ChatPollModel).where(ChatPollModel.message_id.in_(message_ids))
        )
        for poll in r.scalars().all():
            out[poll.message_id] = poll
        return out

    async def votes_for_poll_ids(self, poll_ids: list[int]) -> dict[int, list[ChatPollVoteModel]]:
        out: dict[int, list[ChatPollVoteModel]] = {}
        if not poll_ids:
            return out
        r = await self._session.execute(
            select(ChatPollVoteModel)
            .where(ChatPollVoteModel.poll_id.in_(poll_ids))
            .order_by(ChatPollVoteModel.poll_id, ChatPollVoteModel.option_index, ChatPollVoteModel.id)
        )
        for vote in r.scalars().all():
            out.setdefault(vote.poll_id, []).append(vote)
        return out

    async def get_poll(self, poll_id: int) -> ChatPollModel | None:
        r = await self._session.execute(select(ChatPollModel).where(ChatPollModel.id == poll_id))
        return r.scalars().one_or_none()

    async def get_poll_by_message_id(self, message_id: int) -> ChatPollModel | None:
        r = await self._session.execute(
            select(ChatPollModel).where(ChatPollModel.message_id == message_id)
        )
        return r.scalars().one_or_none()

    async def create_poll_message(
        self,
        user_id: int,
        room_id: int,
        *,
        kind: str,
        question: str,
        options: list[str],
        allows_multiple: bool = False,
        is_anonymous: bool = False,
        correct_option_index: int | None = None,
        explanation: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> tuple[ChatMessageModel, ChatPollModel] | None:
        if not await self.can_user_post(user_id, room_id):
            return None
        msg_kind = MESSAGE_KIND_QUIZ if kind == POLL_KIND_QUIZ else MESSAGE_KIND_POLL
        if kind == POLL_KIND_QUIZ:
            if correct_option_index is None or correct_option_index < 0 or correct_option_index >= len(options):
                return None
            allows_multiple = False
        body = question.strip()[:4000]
        msg = await self.create_message(
            user_id,
            room_id,
            body,
            reply_to_message_id=reply_to_message_id,
            message_kind=msg_kind,
        )
        if not msg:
            return None
        now = _utc_now()
        poll = ChatPollModel(
            message_id=msg.id,
            kind=kind,
            question=question.strip()[:500],
            options_json=json.dumps(options, ensure_ascii=False),
            allows_multiple=allows_multiple,
            is_anonymous=is_anonymous,
            is_closed=False,
            correct_option_index=correct_option_index if kind == POLL_KIND_QUIZ else None,
            explanation=(explanation or None),
            created_at=now,
        )
        self._session.add(poll)
        await self._session.flush()
        return msg, poll

    async def vote_poll(
        self,
        user_id: int,
        poll_id: int,
        option_index: int,
    ) -> tuple[ChatPollModel, list[ChatPollVoteModel]] | None:
        poll = await self.get_poll(poll_id)
        if not poll or poll.is_closed:
            return None
        r = await self._session.execute(
            select(ChatMessageModel).where(ChatMessageModel.id == poll.message_id)
        )
        msg = r.scalars().one_or_none()
        if not msg or await self.is_member(user_id, msg.room_id) is None:
            return None
        options = json.loads(poll.options_json)
        if option_index < 0 or option_index >= len(options):
            return None
        existing_r = await self._session.execute(
            select(ChatPollVoteModel).where(
                ChatPollVoteModel.poll_id == poll_id,
                ChatPollVoteModel.user_id == user_id,
            )
        )
        existing = list(existing_r.scalars().all())
        if poll.allows_multiple:
            dup = next((v for v in existing if v.option_index == option_index), None)
            if dup:
                await self._session.delete(dup)
            else:
                self._session.add(
                    ChatPollVoteModel(
                        poll_id=poll_id,
                        user_id=user_id,
                        option_index=option_index,
                        created_at=_utc_now(),
                    )
                )
        else:
            for v in existing:
                await self._session.delete(v)
            self._session.add(
                ChatPollVoteModel(
                    poll_id=poll_id,
                    user_id=user_id,
                    option_index=option_index,
                    created_at=_utc_now(),
                )
            )
        await self._session.flush()
        votes_r = await self._session.execute(
            select(ChatPollVoteModel).where(ChatPollVoteModel.poll_id == poll_id)
        )
        return poll, list(votes_r.scalars().all())

    async def close_poll(self, user_id: int, poll_id: int) -> ChatPollModel | None:
        poll = await self.get_poll(poll_id)
        if not poll:
            return None
        r = await self._session.execute(
            select(ChatMessageModel).where(ChatMessageModel.id == poll.message_id)
        )
        msg = r.scalars().one_or_none()
        if not msg:
            return None
        member = await self.is_member(user_id, msg.room_id)
        if not member:
            return None
        if msg.author_user_id != user_id and member.role != MEMBER_ROLE_ADMIN:
            return None
        poll.is_closed = True
        self._session.add(poll)
        await self._session.flush()
        return poll


class HealthRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def check(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
