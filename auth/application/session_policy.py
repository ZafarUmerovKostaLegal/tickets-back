from __future__ import annotations


def session_jti_is_valid(
    token_jti: str | None,
    active_jtis: list[str],
    *,
    legacy_jti: str | None = None,
) -> bool:
    """True if JWT jti is allowed for this user (multi-device + legacy single-column fallback)."""
    allowed = [j for j in active_jtis if j]
    legacy = (legacy_jti or "").strip()
    if legacy and legacy not in allowed:
        allowed.append(legacy)
    if not allowed:
        return not token_jti
    return bool(token_jti and token_jti in allowed)


def jtis_to_evict_after_register(existing_asc: list[str], max_sessions: int) -> list[str]:
    """Return oldest JTIs to remove after appending a new session (FIFO)."""
    if max_sessions < 1:
        max_sessions = 1
    overflow = max(0, len(existing_asc) + 1 - max_sessions)
    return existing_asc[:overflow]
