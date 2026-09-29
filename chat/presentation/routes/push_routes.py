from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id

router = APIRouter(prefix="/push", tags=["chat-push"])


class PushKeysIn(BaseModel):
    p256dh: str = Field(min_length=8, max_length=255)
    auth: str = Field(min_length=8, max_length=255)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=12, max_length=2000)
    keys: PushKeysIn


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=12, max_length=2000)


@router.get("/vapid-public-key")
async def vapid_public_key(
    _user_id: Annotated[int, Depends(get_current_user_id)],
):
    settings = get_settings()
    public_key = (settings.chat_vapid_public_key or "").strip()
    private_key = (settings.chat_vapid_private_key or "").strip()
    enabled = bool(public_key and private_key)
    return {"enabled": enabled, "publicKey": public_key if enabled else ""}


@router.put("/subscription", status_code=204)
async def save_push_subscription(
    body: PushSubscriptionIn,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    endpoint = body.endpoint.strip()
    if not endpoint.startswith("https://"):
        raise HTTPException(status_code=400, detail="Push endpoint must be https")
    repo = ChatRepository(session)
    await repo.upsert_push_subscription(user_id, endpoint, body.keys.p256dh.strip(), body.keys.auth.strip())
    await session.commit()


@router.delete("/subscription", status_code=204)
async def delete_push_subscription(
    body: PushUnsubscribeIn,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    await repo.delete_push_subscription(user_id, body.endpoint.strip())
    await session.commit()
