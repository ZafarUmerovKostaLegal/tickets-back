from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
import hashlib
import hmac
import time


DEFAULT_STATE_MAX_AGE_SECONDS = 3600


def resolve_oauth_state_secret(
    state_secret: str | None,
    fallback_secret: str | None = None,
) -> str:
    return ((state_secret or "").strip() or (fallback_secret or "").strip())


def encode_oauth_state(
    user_id: int,
    secret: str,
    *,
    issued_at: int | None = None,
) -> str:
    if not secret:
        raise ValueError("OAuth state secret is required")
    issued = int(time.time()) if issued_at is None else int(issued_at)
    payload = f"{user_id}:{issued}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{payload}:{signature}"
    return urlsafe_b64encode(token.encode()).decode()


def decode_oauth_state(
    state: str,
    secret: str,
    *,
    max_age_seconds: int = DEFAULT_STATE_MAX_AGE_SECONDS,
) -> int | None:
    try:
        if not secret:
            return None
        raw = urlsafe_b64decode(state.encode()).decode()
        user_id_raw, issued_at_raw, signature = raw.split(":", 2)
        payload = f"{user_id_raw}:{issued_at_raw}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        issued_at = int(issued_at_raw)
        if issued_at <= 0 or int(time.time()) - issued_at > max_age_seconds:
            return None
        return int(user_id_raw)
    except Exception:
        return None
