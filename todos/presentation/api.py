from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.models import (
    OutlookCalendarTokenModel,
    TodoBoardInviteModel,
    TodoBoardLabelModel,
    TodoBoardMemberModel,
    TodoBoardModel,
    TodoCardAttachmentModel,
    TodoCardCommentModel,
    TodoCardChecklistItemModel,
    TodoCardLabelModel,
    TodoCardModel,
    TodoCardParticipantModel,
    TodoColumnModel,
)
from presentation.routes import board_routes, boards_multi_routes, calendar_routes, health
from infrastructure.database import Base, async_session_factory, engine
from infrastructure.repositories import OutlookCalendarTokenRepository
from infrastructure.schema_patches import (
    apply_todo_board_columns_collapsed_patch,
    apply_todo_boards_multi_user_patch,
    apply_todo_kanban_extended_patch,
)

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await apply_todo_board_columns_collapsed_patch(conn)
        await apply_todo_kanban_extended_patch(conn)
        await apply_todo_boards_multi_user_patch(conn)
    try:
        async with async_session_factory() as session:
            n = await OutlookCalendarTokenRepository(session).reencrypt_plaintext_tokens()
            if n:
                _log.info("Re-encrypted %s Outlook calendar token row(s) at rest", n)
    except Exception:
        _log.exception("Outlook token re-encrypt on startup failed (non-fatal)")
    yield


app = FastAPI(
    title="Kosta Todos",
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
app.include_router(calendar_routes.router, prefix="/api/v1/todos")
app.include_router(board_routes.router, prefix="/api/v1/todos")
app.include_router(boards_multi_routes.boards_router, prefix="/api/v1/todos")
app.include_router(boards_multi_routes.invites_router, prefix="/api/v1/todos")
