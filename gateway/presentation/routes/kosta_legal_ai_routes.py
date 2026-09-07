from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings
from infrastructure.kosta_legal_ai import build_instructions, extract_output_text

router = APIRouter(prefix="/api/v1/kosta-legal-ai", tags=["kosta_legal_ai"])

_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
_MAX_QUERY_LEN = 20_000
_MAX_HISTORY = 12


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=_MAX_QUERY_LEN)


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=_MAX_QUERY_LEN)
    law_area: str = "civil"
    source_count: int = Field(default=5, ge=1, le=20)
    web_search: bool = False
    command_id: str | None = None
    messages: list[ChatTurn] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    model: str


def _openai_error_detail(status: int, body: dict[str, Any] | None) -> str:
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            msg = err["message"].strip()
    if status in (401, 403):
        return "OpenAI отклонил ключ доступа. Проверьте OPENAI_API_KEY на gateway."
    if status == 429:
        return "Превышен лимит запросов к OpenAI. Подождите и повторите."
    if status == 404:
        return "Модель OpenAI недоступна для этого ключа. Проверьте OPENAI_MODEL (ожидается gpt-6-astra)."
    if msg and len(msg) < 280:
        return f"OpenAI вернул ошибку: {msg}"
    return "Не удалось получить ответ модели. Попробуйте ещё раз."


def _input_from_request(body: ChatRequest) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for turn in body.messages[-_MAX_HISTORY:]:
        text = turn.content.strip()
        if not text:
            continue
        items.append({"role": turn.role, "content": text})
    query = body.query.strip()
    if not items or items[-1]["role"] != "user" or items[-1]["content"] != query:
        items.append({"role": "user", "content": query})
    return items


async def _create_response(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    instructions: str,
    input_items: list[dict[str, str]],
    use_web_search: bool,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "reasoning": {"effort": "medium"},
    }
    if use_web_search:
        payload["tools"] = [{"type": "web_search"}]
    return await client.post(
        _OPENAI_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )


@router.post("/chat", response_model=ChatResponse)
async def kosta_legal_ai_chat(
    request: Request,
    body: ChatRequest,
    authorization: str | None = Header(None, alias="Authorization"),
):
    await verify_bearer_and_get_user(request, authorization)
    settings = get_settings()
    api_key = (settings.openai_api_key or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Kosta Legal AI не настроен: задайте OPENAI_API_KEY на gateway.",
        )
    model = (settings.openai_model or "gpt-6-astra").strip() or "gpt-6-astra"
    instructions = build_instructions(body.law_area, body.source_count, body.command_id)
    input_items = _input_from_request(body)

    timeout = httpx.Timeout(180.0, connect=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await _create_response(
                client,
                api_key=api_key,
                model=model,
                instructions=instructions,
                input_items=input_items,
                use_web_search=body.web_search,
            )
            if body.web_search and r.status_code in (400, 422):
                r = await _create_response(
                    client,
                    api_key=api_key,
                    model=model,
                    instructions=instructions,
                    input_items=input_items,
                    use_web_search=False,
                )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Не удалось связаться с OpenAI.") from None

    data: dict[str, Any] | None = None
    try:
        parsed = r.json()
        if isinstance(parsed, dict):
            data = parsed
    except ValueError:
        data = None

    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=_openai_error_detail(r.status_code, data))

    answer = extract_output_text(data or {})
    if not answer:
        raise HTTPException(status_code=502, detail="Модель вернула пустой ответ.")
    return ChatResponse(answer=answer, model=model)
