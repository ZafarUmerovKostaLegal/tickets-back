import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.bot_runner import build_bot_application
from infrastructure.config import get_settings
from presentation.routes import health

_log = logging.getLogger("telegram_bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    token = (settings.telegram_bot_token or "").strip()
    bot_app = None
    if token:
        bot_app = build_bot_application(token)
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        _log.info("Telegram bot polling started")
    else:
        _log.warning("TELEGRAM_BOT_TOKEN is empty — bot disabled, only /health")
    try:
        yield
    finally:
        if bot_app is not None:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
            _log.info("Telegram bot stopped")


app = FastAPI(
    title="Kosta Telegram Bot",
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
