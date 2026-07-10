"""At-rest encryption for Outlook OAuth tokens (Fernet).

When OUTLOOK_TOKEN_FERNET_KEY is unset, tokens are stored/read as plaintext
(dev fallback). When set: encrypt on write; decrypt on read; plaintext rows
remain readable and are re-encrypted on startup (no data loss).
"""

from __future__ import annotations

import logging

from infrastructure.config import get_settings

logger = logging.getLogger(__name__)

_PREFIX = "fernet:v1:"


def _fernet():
    key = (get_settings().outlook_token_fernet_key or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception:
        logger.exception("Invalid OUTLOOK_TOKEN_FERNET_KEY; storing tokens as plaintext")
        return None


def encryption_enabled() -> bool:
    return _fernet() is not None


def is_encrypted_token(value: str | None) -> bool:
    return bool(value) and str(value).startswith(_PREFIX)


def encrypt_token(value: str) -> str:
    raw = value or ""
    f = _fernet()
    if f is None or not raw:
        return raw
    if raw.startswith(_PREFIX):
        return raw
    token = f.encrypt(raw.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_token(value: str) -> str:
    raw = value or ""
    if not raw.startswith(_PREFIX):
        return raw
    f = _fernet()
    if f is None:
        logger.warning("Encrypted Outlook token present but OUTLOOK_TOKEN_FERNET_KEY unset")
        return raw
    try:
        blob = raw[len(_PREFIX) :].encode("ascii")
        return f.decrypt(blob).decode("utf-8")
    except Exception:
        logger.exception("Failed to decrypt Outlook token; returning stored value")
        return raw


def warn_if_outlook_fernet_key_empty(secret: str | None, *, service: str = "todos") -> None:
    if (secret or "").strip():
        return
    logger.warning(
        "%s: OUTLOOK_TOKEN_FERNET_KEY is empty — Outlook OAuth tokens are stored "
        "as plaintext. Set a Fernet key in production "
        "(python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\").",
        service,
    )
