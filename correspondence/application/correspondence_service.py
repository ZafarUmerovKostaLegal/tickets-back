
ALLOWED_DIRECTIONS = frozenset({"incoming", "outgoing"})
ALLOWED_DOC_TYPES = frozenset({"letter", "contract", "note"})
ALLOWED_STATUSES = frozenset(
    {
        "draft",
        "pending_review",
        "rejected",
        "new",
        "progress",
        "approval",
        "done",
    }
)
ALLOWED_ATTACHMENT_KINDS = frozenset({"scan", "attachment"})

ALLOWED_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "application/pdf",
    }
)

# Registered workflow + partner review queue
WORK_STATUS_GROUP = frozenset({"progress", "approval", "pending_review"})
REVIEW_EDITABLE_STATUSES = frozenset({"draft", "rejected"})
UNREGISTERED_STATUSES = frozenset({"draft", "pending_review", "rejected"})

REGISTRY_PREFIX = {"incoming": "ВХ", "outgoing": "ИСХ"}


def _normalize_role_key(role: str | None) -> str:
    return (role or "").strip().casefold().replace("ё", "е")


def is_partner_org_role(role: str | None, position: str | None = None) -> bool:
    """Match partner by role or position (RU/EN, substring — same idea as frontend)."""
    kr = _normalize_role_key(role)
    if "партнер" in kr or "partner" in kr:
        return True
    kp = _normalize_role_key(position)
    return "партнер" in kp or "partner" in kp


def normalize_doc_type(value: str | None) -> str:
    v = (value or "letter").strip().lower()
    if v not in ALLOWED_DOC_TYPES:
        raise ValueError(f"docType must be one of: {', '.join(sorted(ALLOWED_DOC_TYPES))}")
    return v


def normalize_status(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v not in ALLOWED_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(ALLOWED_STATUSES))}")
    return v


def parse_status_filter(status: str | None, status_group: str | None) -> list[str] | None:
    if status_group and status_group.strip().lower() == "work":
        return sorted(WORK_STATUS_GROUP)
    if not status or not status.strip():
        return None
    parts = [s.strip().lower() for s in status.split(",") if s.strip()]
    out: list[str] = []
    for p in parts:
        if p not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid status filter: {p}")
        if p not in out:
            out.append(p)
    return out or None


def parse_doc_type_filter(doc_type: str | None) -> list[str] | None:
    if not doc_type or not doc_type.strip():
        return None
    parts = [s.strip().lower() for s in doc_type.split(",") if s.strip()]
    out: list[str] = []
    for p in parts:
        if p not in ALLOWED_DOC_TYPES:
            raise ValueError(f"Invalid docType filter: {p}")
        if p not in out:
            out.append(p)
    return out or None


def format_registry_number(direction: str, year: int, seq: int) -> str:
    prefix = REGISTRY_PREFIX.get(direction, direction.upper())
    return f"{prefix}-{year}/{seq:04d}"


def sniff_mime(content: bytes, declared: str | None) -> str | None:
    decl = (declared or "").split(";")[0].strip().lower()
    if decl == "image/jpg":
        decl = "image/jpeg"
    if len(content) >= 4 and content[:4] == b"%PDF":
        return "application/pdf"
    if len(content) >= 3 and content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(content) >= 8 and content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        brand = content[8:12].lower()
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
            return "image/heic"
    if decl in ALLOWED_MIME_TYPES:
        return decl
    return None


def validate_upload_content(content: bytes, declared_mime: str | None) -> str:
    if not content:
        raise ValueError("Empty file")
    mime = sniff_mime(content, declared_mime)
    if mime:
        return mime
    decl = (declared_mime or "").split(";")[0].strip().lower()
    if decl:
        return decl
    return "application/octet-stream"


def normalize_attachment_kind(value: str | None, *, default: str) -> str:
    k = (value or default).strip().lower()
    if k not in ALLOWED_ATTACHMENT_KINDS:
        raise ValueError("attachmentKind must be scan or attachment")
    return k
