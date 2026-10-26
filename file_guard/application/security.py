from __future__ import annotations

import hashlib
import hmac

MIN_TOKEN_LENGTH = 32


def agent_token_configured(token: str) -> bool:
    return len((token or "").strip()) >= MIN_TOKEN_LENGTH


def agent_token_matches(presented: str, expected: str) -> bool:
    raw = (presented or "").strip()
    secret = (expected or "").strip()
    if not agent_token_configured(secret) or not raw:
        return False
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    presented_hash = hashlib.sha256(raw.encode("utf-8")).digest()
    expected_hash = hashlib.sha256(secret.encode("utf-8")).digest()
    return hmac.compare_digest(presented_hash, expected_hash)


def safe_file_name(name: str) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1].strip()
    base = base.replace("\x00", "").replace("\u202e", "").strip().rstrip(". ")
    if not base or base in {".", ".."}:
        return "file"
    cleaned = "".join(ch if ch.isprintable() and ch not in '<>:"|?*' else "_" for ch in base)
    cleaned = cleaned.strip().rstrip(". ")
    return cleaned[:180] or "file"
