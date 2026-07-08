
from typing import Optional

import jwt


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
