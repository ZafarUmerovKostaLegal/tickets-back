"""HMAC-signed download tokens for QR / public file links (same idea as expenses email_action_token).

v3 compact tokens keep QR module count low for phone cameras.
v1/v2 JSON tokens remain verifiable for older links.
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
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
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


def _uuid_from_hex32(value: str) -> str:
    h = (value or "").strip().lower()
    if not _HEX32_RE.match(h):
        raise ValueError("Invalid id")
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _sig16(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return digest[:16].hex()


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
    n = secrets.token_hex(4)
    if attachment_id:
        aid = _assert_id(attachment_id, "attachment_id")
        msg = f"1|{did}|{aid}|{exp}|{n}"
        return f"1.{did.replace('-', '')}.{aid.replace('-', '')}.{exp}.{n}.{_sig16(secret, msg)}"
    msg = f"2|{did}|{exp}|{n}"
    return f"2.{did.replace('-', '')}.{exp}.{n}.{_sig16(secret, msg)}"


def verify_download_token(
    secret: str,
    *,
    token: str,
    document_id: str,
    attachment_id: str | None = None,
) -> str | None:
    """Verify token. Returns bound attachment_id for attachment-scoped tokens, or None for document-scoped."""
    if not (secret or "").strip():
        raise ValueError("Секрет не настроен")
    raw_token = (token or "").strip()
    if not raw_token or len(raw_token) > _MAX_TOKEN_LEN:
        raise ValueError("Недействительная ссылка")
    did = _assert_id(document_id, "document_id")
    parts = raw_token.split(".")
    if len(parts) == 5 and parts[0] == "2":
        return _verify_compact_v2(secret, parts=parts, document_id=did)
    if len(parts) == 6 and parts[0] == "1":
        return _verify_compact_v1(
            secret,
            parts=parts,
            document_id=did,
            attachment_id=attachment_id,
        )
    if len(parts) == 2:
        return _verify_legacy_json(
            secret,
            body_b64=parts[0],
            sig=parts[1],
            document_id=did,
            attachment_id=attachment_id,
        )
    raise ValueError("Недействительная ссылка")


def _verify_compact_v2(secret: str, *, parts: list[str], document_id: str) -> None:
    _, did_hex, exp_s, n, sig = parts
    try:
        token_did = _uuid_from_hex32(did_hex)
        exp = int(exp_s)
    except ValueError as e:
        raise ValueError("Недействительная ссылка") from e
    if token_did != document_id:
        raise ValueError("Недействительная ссылка")
    if not re.fullmatch(r"[0-9a-f]{8}", n or "", flags=re.IGNORECASE):
        raise ValueError("Недействительная ссылка")
    if not re.fullmatch(r"[0-9a-f]{32}", sig or "", flags=re.IGNORECASE):
        raise ValueError("Недействительная ссылка")
    if int(time.time()) > exp:
        raise ValueError("Ссылка устарела")
    expected = _sig16(secret, f"2|{document_id}|{exp}|{n}")
    if not hmac.compare_digest(expected, sig.lower()):
        raise ValueError("Недействительная ссылка")
    return None


def _verify_compact_v1(
    secret: str,
    *,
    parts: list[str],
    document_id: str,
    attachment_id: str | None,
) -> str:
    _, did_hex, aid_hex, exp_s, n, sig = parts
    try:
        token_did = _uuid_from_hex32(did_hex)
        token_aid = _uuid_from_hex32(aid_hex)
        exp = int(exp_s)
    except ValueError as e:
        raise ValueError("Недействительная ссылка") from e
    if token_did != document_id:
        raise ValueError("Недействительная ссылка")
    if attachment_id is not None and token_aid != attachment_id:
        raise ValueError("Недействительная ссылка")
    if not re.fullmatch(r"[0-9a-f]{8}", n or "", flags=re.IGNORECASE):
        raise ValueError("Недействительная ссылка")
    if not re.fullmatch(r"[0-9a-f]{32}", sig or "", flags=re.IGNORECASE):
        raise ValueError("Недействительная ссылка")
    if int(time.time()) > exp:
        raise ValueError("Ссылка устарела")
    expected = _sig16(secret, f"1|{document_id}|{token_aid}|{exp}|{n}")
    if not hmac.compare_digest(expected, sig.lower()):
        raise ValueError("Недействительная ссылка")
    return token_aid


def _verify_legacy_json(
    secret: str,
    *,
    body_b64: str,
    sig: str,
    document_id: str,
    attachment_id: str | None,
) -> str | None:
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
    if body.get("did") != document_id:
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
    return None
