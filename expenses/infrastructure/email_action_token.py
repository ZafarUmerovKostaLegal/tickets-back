

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Literal

Action = Literal["approve", "reject"]


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def sign_email_action_token(
    secret: str,
    *,
    expense_id: str,
    action: Action,
    ttl_seconds: int,
) -> str:
    if not (secret or "").strip():
        raise ValueError("secret required")
    exp = int(time.time()) + int(ttl_seconds)
    payload = json.dumps(
        {"eid": expense_id, "act": action, "exp": exp, "v": 1},
        separators=(",", ":"),
        sort_keys=True,
    )
    body_b64 = _b64encode(payload.encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body_b64}.{sig}"


def verify_email_action_token(secret: str, *, token: str, expense_id: str) -> Action:

    if not (secret or "").strip():
        raise ValueError("Секрет не настроен")
    parts = (token or "").strip().split(".")
    if len(parts) != 2:
        raise ValueError("Недействительная ссылка")
    body_b64, sig = parts
    expected_sig = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        raise ValueError("Недействительная ссылка")
    try:
        raw = _b64decode(body_b64)
        body = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError("Недействительная ссылка") from e
    if body.get("eid") != expense_id:
        raise ValueError("Ссылка не с этой заявкой")
    exp = int(body.get("exp") or 0)
    if int(time.time()) > exp:
        raise ValueError("Ссылка устарела — откройте заявку в системе")
    act = body.get("act")
    if act not in ("approve", "reject"):
        raise ValueError("Недействительная ссылка")
    return act


def sign_attachment_view_token(
    secret: str,
    *,
    expense_id: str,
    attachment_id: str,
    ttl_seconds: int,
) -> str:
    if not (secret or "").strip():
        raise ValueError("secret required")
    exp = int(time.time()) + int(ttl_seconds)
    payload = json.dumps(
        {"eid": expense_id, "aid": attachment_id, "act": "view", "exp": exp, "v": 2},
        separators=(",", ":"),
        sort_keys=True,
    )
    body_b64 = _b64encode(payload.encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body_b64}.{sig}"


def verify_attachment_view_token(
    secret: str,
    *,
    token: str,
    expense_id: str,
    attachment_id: str,
) -> None:
    if not (secret or "").strip():
        raise ValueError("Секрет не настроен")
    parts = (token or "").strip().split(".")
    if len(parts) != 2:
        raise ValueError("Недействительная ссылка")
    body_b64, sig = parts
    expected_sig = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        raise ValueError("Недействительная ссылка")
    try:
        raw = _b64decode(body_b64)
        body = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError("Недействительная ссылка") from e
    if body.get("eid") != expense_id:
        raise ValueError("Ссылка не с этой заявкой")
    if body.get("aid") != attachment_id:
        raise ValueError("Недействительная ссылка")
    if body.get("act") != "view":
        raise ValueError("Недействительная ссылка")
    exp = int(body.get("exp") or 0)
    if int(time.time()) > exp:
        raise ValueError("Ссылка устарела — откройте заявку в системе")
