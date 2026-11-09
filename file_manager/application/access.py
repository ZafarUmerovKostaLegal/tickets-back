"""Matter-centric DMS access rules (MacroView-style matter security)."""

from __future__ import annotations

LEVELS: tuple[str, ...] = ("none", "read", "edit", "manage")
ACL_LEVELS: frozenset[str] = frozenset({"read", "edit", "manage", "deny"})
REQUEST_LEVELS: frozenset[str] = frozenset({"read", "edit"})

DEFAULT_TEMPLATE: tuple[str, ...] = (
    "Correspondence",
    "Correspondence/Emails",
    "Agreements",
    "Court Documents",
    "Corporate Documents",
    "Research & Memos",
    "Drafts",
    "Billing",
)

_SUPER_ADMIN_ROLES = frozenset({"главный администратор", "администратор"})


def _norm(value: str | None) -> str:
    return " ".join((value or "").strip().lower().replace("ё", "е").replace("-", " ").split())


def is_super_admin(role: str | None) -> bool:
    return _norm(role) in _SUPER_ADMIN_ROLES


def is_partner(role: str | None, position: str | None) -> bool:
    for raw in (role, position):
        key = _norm(raw)
        if "партнер" in key or "partner" in key:
            return True
    return False


def is_manager(role: str | None, position: str | None) -> bool:
    """Admins and partners manage the DMS (same rule as the front admin panel)."""
    return is_super_admin(role) or is_partner(role, position)


def level_rank(level: str | None) -> int:
    try:
        return LEVELS.index(level or "none")
    except ValueError:
        return 0


def has_level(level: str | None, need: str) -> bool:
    return level_rank(level) >= level_rank(need)


def resolve_matter_level(
    *,
    manager: bool,
    super_admin: bool,
    tt_access: bool,
    acl_level: str | None,
    matter_closed: bool,
) -> str:
    """
    Explicit ACL wins over access inherited from time tracking.
    ``deny`` is an ethical wall: it hides the matter from everyone except super admins.
    Closed (archived) matters are read-only for everyone who is not a manager.
    """
    if acl_level == "deny" and not super_admin:
        return "none"
    if manager:
        return "manage"
    if acl_level in ACL_LEVELS and acl_level != "deny":
        level = acl_level
    elif tt_access:
        level = "edit"
    else:
        level = "none"
    if matter_closed and level_rank(level) > level_rank("read"):
        return "read"
    return level


def normalize_template(raw: object) -> list[str]:
    """Folder paths like ``Correspondence/Emails``; parents are added automatically."""
    if not isinstance(raw, list):
        return list(DEFAULT_TEMPLATE)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        parts = [part.strip()[:120] for part in item.replace("\\", "/").split("/")]
        parts = [part for part in parts if part and part not in {".", ".."}]
        if not parts:
            continue
        for depth in range(1, min(len(parts), 4) + 1):
            path = "/".join(parts[:depth])
            key = path.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
        if len(out) >= 60:
            break
    return out


def file_kind(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        return "image"
    if lowered.endswith((".doc", ".docx", ".rtf", ".odt")):
        return "doc"
    if lowered.endswith((".xls", ".xlsx", ".csv", ".ods")):
        return "sheet"
    if lowered.endswith((".ppt", ".pptx")):
        return "slides"
    if lowered.endswith((".msg", ".eml")):
        return "email"
    if lowered.endswith((".zip", ".rar", ".7z")):
        return "archive"
    if lowered.endswith((".txt", ".md")):
        return "text"
    return "file"


def safe_name(raw: str | None, fallback: str = "file") -> str:
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    name = "".join(ch for ch in name if ch >= " ")
    return name[:300] or fallback
