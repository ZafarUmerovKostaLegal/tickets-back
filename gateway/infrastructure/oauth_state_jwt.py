

from typing import Literal, Optional

import jwt

OAuthTarget = Literal["main", "admin"]


def parse_oauth_state_token(
    state: str | None,
    *,
    jwt_secret: str,
    jwt_algorithm: str,
) -> Optional[OAuthTarget]:
    if not state or not (jwt_secret or "").strip():
        return None
    try:
        p = jwt.decode(state.strip(), jwt_secret, algorithms=[jwt_algorithm])
        if not p.get("oauth_st"):
            return None
        t = (p.get("t") or "main").strip()
        return "admin" if t == "admin" else "main"
    except Exception:
        return None
