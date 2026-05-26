from __future__ import annotations

import base64
import hmac
import json
import time
from hashlib import sha256
from typing import Any


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_email_action_token(
    secret: str,
    *,
    request_id: int,
    action: str,
    ttl_seconds: int,
) -> str:
    if not secret or not secret.strip():
        raise ValueError("VACATION_EMAIL_ACTION_SECRET is not set")
    if action not in ("approve", "decline"):
        raise ValueError("action must be 'approve' or 'decline'")
    payload: dict[str, Any] = {
        "rid": int(request_id),
        "act": action,
        "exp": int(time.time()) + int(ttl_seconds),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, sha256).digest()
    return f"{_b64url_encode(body)}.{_b64url_encode(sig)}"


def verify_email_action_token(secret: str, token: str) -> dict[str, Any]:
    if not secret or not secret.strip():
        raise ValueError("VACATION_EMAIL_ACTION_SECRET is not set on server")
    if not token or "." not in token:
        raise ValueError("Bad token format")
    body_b64, sig_b64 = token.split(".", 1)
    try:
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise ValueError(f"Cannot decode token: {exc}") from exc
    expected = hmac.new(secret.encode("utf-8"), body, sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Bad token signature")
    payload = json.loads(body.decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Token expired")
    if payload.get("act") not in ("approve", "decline"):
        raise ValueError("Bad action in token")
    return payload
