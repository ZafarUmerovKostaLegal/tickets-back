

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


async def apply_todo_boards_multi_user_patch(conn: AsyncConnection) -> None:

    stmts = [
        "ALTER TABLE todo_boards ADD COLUMN IF NOT EXISTS title VARCHAR(200) NOT NULL DEFAULT 'Моя доска'",
        (
            "ALTER TABLE todo_boards ADD COLUMN IF NOT EXISTS visibility VARCHAR(32) NOT NULL DEFAULT 'personal'"
        ),
        "ALTER TABLE todo_boards ADD COLUMN IF NOT EXISTS color VARCHAR(32)",
        "ALTER TABLE todo_boards ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE todo_boards ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
    ]
    for s in stmts:
        await conn.execute(text(s))

    await conn.execute(
        text("ALTER TABLE todo_boards DROP CONSTRAINT IF EXISTS todo_boards_user_id_key")
    )
    await conn.execute(
        text("ALTER TABLE todo_boards DROP CONSTRAINT IF EXISTS todo_boards_user_id_uniq")
    )
    await conn.execute(
        text(
            """
            DO $$
            DECLARE
                con RECORD;
            BEGIN
                FOR con IN
                    SELECT c.conname::text AS cname
                    FROM pg_constraint c
                    JOIN pg_class rel ON rel.oid = c.conrelid
                    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                    WHERE nsp.nspname = 'public'
                      AND rel.relname = 'todo_boards'
                      AND c.contype = 'u'
                      AND array_length(c.conkey, 1) = 1
                      AND EXISTS (
                          SELECT 1 FROM pg_attribute a
                          WHERE a.attrelid = c.conrelid
                            AND a.attnum = c.conkey[1]
                            AND a.attname = 'user_id'
                      )
                LOOP
                    EXECUTE format(
                        'ALTER TABLE public.todo_boards DROP CONSTRAINT IF EXISTS %I',
                        con.cname
                    );
                END LOOP;
            END $$;
            """
        )
    )
    await conn.execute(
        text(
            """
            DO $$
            DECLARE
                idx RECORD;
            BEGIN
                FOR idx IN
                    SELECT quote_ident(n.nspname) || '.' || quote_ident(ic.relname) AS fqname
                    FROM pg_index i
                    JOIN pg_class t ON t.oid = i.indrelid
                    JOIN pg_class ic ON ic.oid = i.indexrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = 'public'
                      AND t.relname = 'todo_boards'
                      AND i.indisunique
                      AND NOT i.indisprimary
                      AND array_length(i.indkey, 1) = 1
                      AND EXISTS (
                          SELECT 1 FROM pg_attribute a
                          WHERE a.attrelid = i.indrelid
                            AND a.attnum = i.indkey[1]
                            AND a.attname = 'user_id'
                      )
                LOOP
                    EXECUTE 'DROP INDEX IF EXISTS ' || idx.fqname;
                END LOOP;
            END $$;
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_todo_boards_user_id ON todo_boards (user_id)")
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS todo_board_members (
                board_id INTEGER NOT NULL REFERENCES todo_boards (id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                role VARCHAR(32) NOT NULL DEFAULT 'editor',
                joined_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (board_id, user_id)
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_todo_board_members_user_id ON todo_board_members (user_id)"
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS todo_board_invites (
                id SERIAL PRIMARY KEY,
                board_id INTEGER NOT NULL REFERENCES todo_boards (id) ON DELETE CASCADE,
                inviter_user_id INTEGER NOT NULL,
                invitee_user_id INTEGER NOT NULL,
                role_offered VARCHAR(32) NOT NULL DEFAULT 'editor',
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                message VARCHAR(500),
                created_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                resolved_at TIMESTAMPTZ
            )
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_todo_board_invites_board_id ON todo_board_invites (board_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_todo_board_invites_invitee ON todo_board_invites (invitee_user_id)"
        )
    )
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_todo_board_invite_pending
                ON todo_board_invites (board_id, invitee_user_id)
                WHERE status = 'pending'
            """
        )
    )
