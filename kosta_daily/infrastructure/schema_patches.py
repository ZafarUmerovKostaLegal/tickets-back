"""Additive chat schema patches — IF NOT EXISTS only."""

from __future__ import annotations

from backend_common.schema_patch_runner import PatchFn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def apply_chat_reply_and_kind_columns(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE chat_messages
            ADD COLUMN IF NOT EXISTS reply_to_message_id BIGINT NULL
            REFERENCES chat_messages(id) ON DELETE SET NULL
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE chat_messages
            ADD COLUMN IF NOT EXISTS message_kind VARCHAR(16) NOT NULL DEFAULT 'text'
            """
        )
    )


async def apply_chat_reactions_table(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_message_reactions (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                emoji VARCHAR(8) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT uq_chat_reaction_per_user UNIQUE (message_id, user_id, emoji)
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chat_message_reactions_message_id "
            "ON chat_message_reactions(message_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chat_message_reactions_user_id "
            "ON chat_message_reactions(user_id)"
        )
    )


async def apply_chat_polls_tables(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_polls (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL UNIQUE REFERENCES chat_messages(id) ON DELETE CASCADE,
                kind VARCHAR(16) NOT NULL DEFAULT 'poll',
                question VARCHAR(500) NOT NULL,
                options_json TEXT NOT NULL,
                allows_multiple BOOLEAN NOT NULL DEFAULT FALSE,
                is_anonymous BOOLEAN NOT NULL DEFAULT FALSE,
                is_closed BOOLEAN NOT NULL DEFAULT FALSE,
                correct_option_index INTEGER NULL,
                explanation VARCHAR(1000) NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_chat_polls_message_id ON chat_polls(message_id)")
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_poll_votes (
                id BIGSERIAL PRIMARY KEY,
                poll_id BIGINT NOT NULL REFERENCES chat_polls(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                option_index INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT uq_chat_poll_vote UNIQUE (poll_id, user_id, option_index)
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_chat_poll_votes_poll_id ON chat_poll_votes(poll_id)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_chat_poll_votes_user_id ON chat_poll_votes(user_id)")
    )


async def apply_chat_checklists_tables(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_checklists (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL UNIQUE REFERENCES chat_messages(id) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                others_can_complete BOOLEAN NOT NULL DEFAULT FALSE,
                others_can_append BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_chat_checklists_message_id ON chat_checklists(message_id)")
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_checklist_items (
                id BIGSERIAL PRIMARY KEY,
                checklist_id BIGINT NOT NULL REFERENCES chat_checklists(id) ON DELETE CASCADE,
                text VARCHAR(200) NOT NULL,
                position INTEGER NOT NULL,
                created_by_user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chat_checklist_items_checklist_id "
            "ON chat_checklist_items(checklist_id)"
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_checklist_completions (
                item_id BIGINT PRIMARY KEY REFERENCES chat_checklist_items(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chat_checklist_completions_user_id "
            "ON chat_checklist_completions(user_id)"
        )
    )


async def apply_chat_pinned_messages_table(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_pinned_messages (
                room_id BIGINT NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
                message_id BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
                pinned_by_user_id BIGINT NOT NULL,
                pinned_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (room_id, message_id)
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chat_pinned_messages_room_pinned_at "
            "ON chat_pinned_messages(room_id, pinned_at DESC)"
        )
    )


async def apply_chat_push_subscriptions_table(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_push_subscriptions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh VARCHAR(255) NOT NULL,
                auth VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chat_push_subscriptions_user_id "
            "ON chat_push_subscriptions(user_id)"
        )
    )


REGISTERED_CHAT_SCHEMA_PATCHES: list[tuple[str, PatchFn]] = [
    ("chat_reply_and_kind_columns", apply_chat_reply_and_kind_columns),
    ("chat_reactions_table", apply_chat_reactions_table),
    ("chat_polls_tables", apply_chat_polls_tables),
    ("chat_checklists_tables", apply_chat_checklists_tables),
    ("chat_pinned_messages_table", apply_chat_pinned_messages_table),
    ("chat_push_subscriptions_table", apply_chat_push_subscriptions_table),
]
