from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.cors_origins import resolve_cors_origins
from presentation.routes import daily_routes, health

KOSTA_DAILY_API_PREFIX = "/api/v1/kosta-daily"

app = FastAPI(
    title="Kosta Daily",
    version="0.1.0",
    description="Микросервис Kosta Daily.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=resolve_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(daily_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
