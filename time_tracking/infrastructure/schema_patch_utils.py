

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from backend_common.sql_injection_guard import validate_sql_identifier


async def execute_sql(conn: AsyncConnection, statements: Iterable[str]) -> None:
    for statement in statements:
        await conn.execute(text(statement))


async def add_columns_if_missing(
    conn: AsyncConnection,
    table_name: str,
    column_definitions: Iterable[str],
) -> None:
    validate_sql_identifier(table_name, kind="table name")
    ddl_lines: list[str] = []
    for column_definition in column_definitions:
        first = column_definition.strip().split(None, 1)[0]
        validate_sql_identifier(first, kind="column name")

        ddl_lines.append(
            f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_definition}"
        )
    await execute_sql(conn, ddl_lines)
