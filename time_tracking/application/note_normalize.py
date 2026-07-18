
from __future__ import annotations

import re
from typing import Any

# Keep in sync with FE INVOICE_DESCRIPTION_TASK_PREFIXES.
KNOWN_TASK_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "Court Hearing Preparation",
            "Court Hearing",
            "Document Submission",
            "Document Review",
            "Drafting Documents",
            "Drafting",
            "Telephone calls",
            "My mehnat registration",
            "Kosta Legal Internal",
            "Business Development",
            "Other research",
            "Review new legislation",
            "Emails",
            "Meetings",
            "Research",
            "Accounting",
            "Lunch/Dinner",
            "Proposals",
            "Publications",
        ),
        key=len,
        reverse=True,
    )
)

_SEP_RE = re.compile(r"^[\s:.\u2014\u2013\-\u2013]+")


def _collapse_ws(value: str) -> str:
    return " ".join(value.strip().split())


def _is_safe_prefix_boundary(after: str) -> bool:
    """True when text after a task label starts a new note (not e.g. Review→Reviewing)."""
    if not after:
        return False
    ch = after[0]
    if ch.isspace() or ch in ":\n.—–-":
        return True
    # Glued Harvest-style: "Document ReviewЗаконодательство"
    if ord(ch) > 127:
        return True
    if ch.isupper():
        return True
    return False


def strip_task_prefix_from_note(raw: str | None, task_name: str | None = None) -> str:
    """Notes only: strip Task\\nNotes storage and leading known / explicit task labels."""
    text = (raw or "").replace("\r\n", "\n").strip()
    if not text:
        return ""

    if "\n" in text:
        first, rest = text.split("\n", 1)
        rest = rest.strip()
        if rest:
            text = rest
        else:
            text = first.strip()

    task = (task_name or "").strip()
    if task and text.lower().startswith(task.lower()):
        after = text[len(task) :]
        if _is_safe_prefix_boundary(after) or not after:
            stripped = _SEP_RE.sub("", after).strip()
            if stripped:
                text = stripped
            elif not after.strip():
                return ""

    lower = text.lower()
    for prefix in KNOWN_TASK_PREFIXES:
        pl = prefix.lower()
        if len(text) <= len(prefix):
            continue
        if not lower.startswith(pl):
            continue
        after = text[len(prefix) :]
        if not _is_safe_prefix_boundary(after):
            continue
        stripped = _SEP_RE.sub("", after).strip()
        if stripped:
            return stripped
    return text


def normalize_note_for_duplicate_key(raw: str | None, task_name: str | None = None) -> str:
    """Canonical note for duplicate fingerprints (case/ws insensitive, task prefix stripped)."""
    return _collapse_ws(strip_task_prefix_from_note(raw, task_name)).lower()


def notes_are_near_duplicate(a: str, b: str, *, min_prefix_len: int = 24) -> bool:
    """Exact match, or one normalized note is a substantial prefix of the other."""
    if a == b:
        return True
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    core = shorter.rstrip(" ,.;:")
    if len(core) < min_prefix_len:
        return False
    return longer.startswith(core)


def task_name_from_row(task: Any | None) -> str | None:
    if task is None:
        return None
    name = getattr(task, "name", None)
    if name is None and isinstance(task, dict):
        name = task.get("name")
    s = (str(name) if name is not None else "").strip()
    return s or None
