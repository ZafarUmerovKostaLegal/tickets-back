"""HMAC-signed download tokens for QR / public file links (same idea as expenses email_action_token).

v2 tokens bind document_id only so the QR stays valid when the letter file is re-uploaded.
v1 tokens (document + attachment) remain verifiable for older links.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import re

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_MAX_TOKEN_LEN = 2048


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _assert_id(value: str, label: str) -> str:
    v = (value or "").strip()
    if not _UUID_RE.match(v):
        raise ValueError(f"Invalid {label}")
    return v


def sign_download_token(
    secret: str,
    *,
    document_id: str,
    attachment_id: str | None = None,
    ttl_seconds: int,
) -> str:
    if not (secret or "").strip():
        raise ValueError("secret required")
    if len(secret.strip()) < 16:
        raise ValueError("secret too short")
    did = _assert_id(document_id, "document_id")
    ttl = int(ttl_seconds)
    if ttl < 60 or ttl > 2_592_000:
        raise ValueError("invalid ttl")
    exp = int(time.time()) + ttl
    payload: dict = {
        "did": did,
        "act": "download",
        "exp": exp,
        "n": secrets.token_hex(8),
        "v": 2 if not attachment_id else 1,
    }
    if attachment_id:
        payload["aid"] = _assert_id(attachment_id, "attachment_id")
    body_b64 = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body_b64}.{sig}"


def verify_download_token(
    secret: str,
    *,
    token: str,
    document_id: str,
    attachment_id: str | None = None,
) -> str | None:
    """Verify token. Returns bound attachment_id for v1 tokens, or None for document-scoped v2."""
    if not (secret or "").strip():
        raise ValueError("Секрет не настроен")
    raw_token = (token or "").strip()
    if not raw_token or len(raw_token) > _MAX_TOKEN_LEN:
        raise ValueError("Недействительная ссылка")
    did = _assert_id(document_id, "document_id")
    parts = raw_token.split(".")
    if len(parts) != 2:
        raise ValueError("Недействительная ссылка")
    body_b64, sig = parts
    if not body_b64 or not sig or len(sig) != 64:
        raise ValueError("Недействительная ссылка")
    expected_sig = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        raise ValueError("Недействительная ссылка")
    try:
        raw = _b64decode(body_b64)
        body = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        raise ValueError("Недействительная ссылка") from e
    if not isinstance(body, dict):
        raise ValueError("Недействительная ссылка")
    if body.get("did") != did:
        raise ValueError("Недействительная ссылка")
    if body.get("act") != "download":
        raise ValueError("Недействительная ссылка")
    try:
        exp = int(body.get("exp") or 0)
    except (TypeError, ValueError) as e:
        raise ValueError("Недействительная ссылка") from e
    if int(time.time()) > exp:
        raise ValueError("Ссылка устарела")

    aid = body.get("aid")
    if aid is not None:
        if not isinstance(aid, str) or not _UUID_RE.match(aid):
            raise ValueError("Недействительная ссылка")
        if attachment_id is not None and aid != attachment_id:
            raise ValueError("Недействительная ссылка")
        return aid
    # v2 document-scoped
    return None
