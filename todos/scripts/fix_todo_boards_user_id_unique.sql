-- Разрешить несколько досок на одного пользователя (снять UNIQUE с todo_boards.user_id).
-- Выполнить в БД todos (например kosta_todos), от имени владельца схемы:
--   psql "$TODOS_DATABASE_URL" -f todos/scripts/fix_todo_boards_user_id_unique.sql

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT c.conname::text AS cname
        FROM pg_constraint c
        WHERE c.conrelid = 'todo_boards'::regclass
          AND c.contype = 'u'
    LOOP
        EXECUTE format(
            'ALTER TABLE todo_boards DROP CONSTRAINT IF EXISTS %I',
            r.cname
        );
    END LOOP;
END $$;

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT ix.oid::regclass::text AS idx_qname
        FROM pg_index i
        JOIN pg_class ix ON ix.oid = i.indexrelid
        WHERE i.indrelid = 'todo_boards'::regclass
          AND i.indisunique
          AND NOT i.indisprimary
    LOOP
        EXECUTE 'DROP INDEX IF EXISTS ' || r.idx_qname;
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS ix_todo_boards_user_id ON todo_boards (user_id);
