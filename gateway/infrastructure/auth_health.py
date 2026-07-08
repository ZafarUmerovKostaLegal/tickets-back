from __future__ import annotations

import httpx

from infrastructure.config import get_settings


async def auth_service_is_healthy(*, timeout_sec: float = 3.0) -> bool:
    base = (get_settings().auth_service_url or "").strip().rstrip("/")
    if not base:
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.get(f"{base}/health")
        if response.status_code != 200:
            return False
        payload = response.json()
        return (payload.get("status") or "").strip().lower() == "healthy"
    except Exception:
        return False
