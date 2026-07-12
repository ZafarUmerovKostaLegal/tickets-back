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


REGISTERED_CHAT_SCHEMA_PATCHES: list[tuple[str, PatchFn]] = [
    ("chat_reply_and_kind_columns", apply_chat_reply_and_kind_columns),
    ("chat_reactions_table", apply_chat_reactions_table),
    ("chat_polls_tables", apply_chat_polls_tables),
]
