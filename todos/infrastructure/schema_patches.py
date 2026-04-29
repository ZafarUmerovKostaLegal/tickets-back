

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def apply_todo_board_columns_collapsed_patch(conn: AsyncConnection) -> None:

    await conn.execute(
        text(
            """
            ALTER TABLE todo_board_columns
            ADD COLUMN IF NOT EXISTS is_collapsed BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )


async def apply_todo_kanban_extended_patch(conn: AsyncConnection) -> None:

    for stmt in (
        "ALTER TABLE todo_board_cards ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ",
        (
            "ALTER TABLE todo_board_cards ADD COLUMN IF NOT EXISTS is_completed "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ),
        (
            "ALTER TABLE todo_board_cards ADD COLUMN IF NOT EXISTS is_archived "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ),
    ):
        await conn.execute(text(stmt))

    ddl = (
        """
        CREATE TABLE IF NOT EXISTS todo_board_labels (
            id SERIAL PRIMARY KEY,
            board_id INTEGER NOT NULL REFERENCES todo_boards (id) ON DELETE CASCADE,
            title VARCHAR(200) NOT NULL,
            color VARCHAR(32) NOT NULL DEFAULT '#6b7280',
            position INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_todo_board_labels_board_id
            ON todo_board_labels (board_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS todo_card_labels (
            card_id INTEGER NOT NULL REFERENCES todo_board_cards (id) ON DELETE CASCADE,
            label_id INTEGER NOT NULL REFERENCES todo_board_labels (id) ON DELETE CASCADE,
            PRIMARY KEY (card_id, label_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS todo_card_checklist_items (
            id SERIAL PRIMARY KEY,
            card_id INTEGER NOT NULL REFERENCES todo_board_cards (id) ON DELETE CASCADE,
            title VARCHAR(500) NOT NULL,
            is_done BOOLEAN NOT NULL DEFAULT FALSE,
            position INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_todo_card_checklist_items_card_id
            ON todo_card_checklist_items (card_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS todo_card_participants (
            card_id INTEGER NOT NULL REFERENCES todo_board_cards (id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (card_id, user_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS todo_card_attachments (
            id SERIAL PRIMARY KEY,
            card_id INTEGER NOT NULL REFERENCES todo_board_cards (id) ON DELETE CASCADE,
            storage_key VARCHAR(1024) NOT NULL,
            original_filename VARCHAR(500) NOT NULL,
            mime_type VARCHAR(200),
            size_bytes INTEGER NOT NULL,
            uploaded_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_todo_card_attachments_card_id
            ON todo_card_attachments (card_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS todo_card_comments (
            id SERIAL PRIMARY KEY,
            card_id INTEGER NOT NULL REFERENCES todo_board_cards (id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_todo_card_comments_card_id
            ON todo_card_comments (card_id)
        """,
    )
    for sql in ddl:
        await conn.execute(text(sql))
