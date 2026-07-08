
from datetime import datetime, timedelta, timezone

import jwt


def create_oauth_state_token(*, jwt_secret: str, jwt_algorithm: str) -> str:
    if not (jwt_secret or "").strip():
        raise ValueError("JWT_SECRET is required to sign OAuth state")
    now = datetime.now(timezone.utc)
    payload = {
        "oauth_st": True,
        "t": "main",
        "exp": now + timedelta(minutes=10),
        "iat": now,
    }
    return jwt.encode(payload, jwt_secret, algorithm=jwt_algorithm)


def parse_oauth_state_token(
    state: str | None,
    *,
    jwt_secret: str,
    jwt_algorithm: str,
) -> bool:
    if not state or not (jwt_secret or "").strip():
        return False
    try:
        p = jwt.decode(state.strip(), jwt_secret, algorithms=[jwt_algorithm])
        return bool(p.get("oauth_st"))
    except Exception:
        return False
