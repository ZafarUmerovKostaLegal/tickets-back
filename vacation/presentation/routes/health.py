from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from infrastructure.config import get_settings
from infrastructure.email_send import email_action_missing, email_action_ready
from infrastructure.database import engine

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime = Field(description="UTC ISO8601")
    database: str = Field(description="ok | not_configured | error")
    email_actions_ready: bool = Field(description="Кнопки Утвердить/Отклонить в письмах")
    email_actions_missing: list[str] = Field(default_factory=list)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    ts = datetime.now(timezone.utc)
    settings = get_settings()
    db_status = "not_configured"
    if engine is not None:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            db_status = "ok"
        except Exception:
            db_status = "error"
    if engine is not None and db_status == "error":
        raise HTTPException(status_code=503, detail="База данных недоступна")
    return HealthResponse(
        status="ok",
        service="vacation",
        timestamp=ts,
        database=db_status,
        email_actions_ready=email_action_ready(settings),
        email_actions_missing=email_action_missing(settings),
    )
