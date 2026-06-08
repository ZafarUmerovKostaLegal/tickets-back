from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.database import Base, engine
from presentation.routes import attachments_routes, health, messages_routes, polls_routes, rooms_routes

CHAT_API_PREFIX = "/api/v1/chat"


async def _ensure_reply_column(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE chat_messages
            ADD COLUMN IF NOT EXISTS reply_to_message_id BIGINT NULL
            REFERENCES chat_messages(id) ON DELETE SET NULL
            """
        )
    )


async def _ensure_message_kind_column(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE chat_messages
            ADD COLUMN IF NOT EXISTS message_kind VARCHAR(16) NOT NULL DEFAULT 'text'
            """
        )
    )


async def _ensure_polls_tables(conn) -> None:
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


async def _ensure_reactions_table(conn) -> None:
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
        text("CREATE INDEX IF NOT EXISTS ix_chat_message_reactions_message_id ON chat_message_reactions(message_id)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_chat_message_reactions_user_id ON chat_message_reactions(user_id)")
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_reply_column(conn)
        await _ensure_message_kind_column(conn)
        await _ensure_reactions_table(conn)
        await _ensure_polls_tables(conn)
    yield


app = FastAPI(
    title="Chat",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.include_router(health.router)
app.include_router(rooms_routes.router, prefix=CHAT_API_PREFIX)
app.include_router(messages_routes.router, prefix=CHAT_API_PREFIX)
app.include_router(polls_routes.router, prefix=CHAT_API_PREFIX)
app.include_router(attachments_routes.router, prefix=CHAT_API_PREFIX)
