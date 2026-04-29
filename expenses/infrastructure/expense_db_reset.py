

from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.database import Base, async_session_factory, engine

_log = logging.getLogger(__name__)


_POST_CREATE_DDLS = (
    "ALTER TABLE expense_requests ADD COLUMN IF NOT EXISTS payment_deadline DATE",
    "ALTER TABLE expense_attachments ADD COLUMN IF NOT EXISTS attachment_kind VARCHAR(64)",
)


async def reset_expenses_database_schema() -> None:

    from infrastructure import models

    from infrastructure.repositories import seed_reference_data

    async with engine.begin() as conn:

        def _drop_and_create(sync_conn) -> None:
            Base.metadata.drop_all(sync_conn)
            Base.metadata.create_all(sync_conn)

        await conn.run_sync(_drop_and_create)
        for ddl in _POST_CREATE_DDLS:
            try:
                await conn.execute(text(ddl))
            except Exception as ex:
                _log.debug("post-reset ddl %s: %s", ddl, ex)

    async with async_session_factory() as session:
        await seed_reference_data(session)
        await session.commit()

    _log.warning("expenses DB: полный сброс схемы выполнен (пересоздание таблиц + seed)")
