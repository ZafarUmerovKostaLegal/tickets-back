from __future__ import annotations


def normalize_role_key(value: str | None) -> str:
    return (
        (value or "")
        .strip()
        .lower()
        .replace("ё", "е")
        .replace("-", " ")
        .replace("  ", " ")
    )


_ADMIN_KEYS = frozenset({"главный администратор", "администратор"})


def can_access_hr(role: str | None, position: str | None = None) -> bool:
    """Администраторы и партнёры. Та же граница, что у вкладки «Бухгалтерия»."""
    for raw in (role, position):
        key = normalize_role_key(raw)
        if not key:
            continue
        if key in _ADMIN_KEYS:
            return True
        if "партнер" in key or "partner" in key:
            return True
    return False
