

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from domain.roles import Role


_USER_COLUMN_PATCHES: Sequence[str] = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS picture VARCHAR(1024)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(64) DEFAULT 'Сотрудник'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_archived BOOLEAN DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS time_tracking_role VARCHAR(32)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS position VARCHAR(256)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS desktop_background VARCHAR(512)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS active_session_jti VARCHAR(64)",
)


def seed_default_roles(sync_conn) -> None:

    result = sync_conn.execute(text("SELECT id FROM roles LIMIT 1"))
    if result.fetchone() is None:
        for role in Role:
            sync_conn.execute(
                text("INSERT INTO roles (name, created_at) VALUES (:name, NOW())"),
                {"name": role.value},
            )

    result = sync_conn.execute(text("SELECT id, name FROM roles"))
    role_ids = {name: role_id for (role_id, name) in result.fetchall()}
    if Role.MAIN_ADMIN.value not in role_ids:
        sync_conn.execute(
            text("INSERT INTO roles (name, created_at) VALUES (:name, NOW())"),
            {"name": Role.MAIN_ADMIN.value},
        )
        result = sync_conn.execute(
            text("SELECT id FROM roles WHERE name = :name"),
            {"name": Role.MAIN_ADMIN.value},
        )
        row = result.fetchone()
        if row:
            role_ids[Role.MAIN_ADMIN.value] = row[0]

    main_admin_id = role_ids.get(Role.MAIN_ADMIN.value)
    for role_name in (
        Role.MAIN_ADMIN.value,
        Role.ADMIN.value,
        Role.PARTNER.value,
        Role.OFFICE_MANAGER.value,
        Role.IT_DEPARTMENT.value,
    ):
        role_id = role_ids.get(role_name)
        if role_id is None:
            continue
        sync_conn.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission_key, allowed) "
                "VALUES (:role_id, 'time_tracking', TRUE) "
                "ON CONFLICT (role_id, permission_key) DO NOTHING"
            ),
            {"role_id": role_id},
        )

    if main_admin_id is not None:
        sync_conn.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission_key, allowed) "
                "VALUES (:role_id, 'time_tracking_admin', TRUE) "
                "ON CONFLICT (role_id, permission_key) DO NOTHING"
            ),
            {"role_id": main_admin_id},
        )

    sync_conn.execute(
        text("UPDATE users SET role = :role WHERE azure_oid = 'local-admin'"),
        {"role": Role.MAIN_ADMIN.value},
    )


async def ensure_auth_schema(conn) -> None:

    for statement in _USER_COLUMN_PATCHES:
        await conn.execute(text(statement))
