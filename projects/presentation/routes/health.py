from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from infrastructure.database import engine

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime = Field(description="UTC ISO8601")
    database: str = Field(
        description="ok | not_configured | error",
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    ts = datetime.now(timezone.utc)
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
        service="projects",
        timestamp=ts,
        database=db_status,
    )
