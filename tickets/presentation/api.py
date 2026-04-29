from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.database import engine, Base
from infrastructure.models import TicketModel, CommentModel
from presentation.routes import health, tickets_routes, ws_tickets


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT false"
        ))
    yield


app = FastAPI(title="Tickets", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.include_router(health.router)
app.include_router(tickets_routes.router)
app.include_router(ws_tickets.router)
