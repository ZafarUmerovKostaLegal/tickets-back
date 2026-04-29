from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from presentation.routes import schedule_routes


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="Call schedule",
    version="0.1.0",
    lifespan=lifespan,
    description="Календари и события общего ящика (Microsoft Graph, без БД).",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(schedule_routes.router, prefix="/api/v1/call-schedule")


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "call_schedule"}