

from __future__ import annotations

import re
from typing import Any


_PLAIN_HTTPS_RE = re.compile(
    r"https?://[^\s<>'\"()]+",
    re.IGNORECASE,
)


def _strip_htmlish(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    t = re.sub(r"</p\s*>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ")
    t = t.replace("&#160;", " ")
    t = t.replace("&amp;", "&")
    t = t.replace("&#58;", ":")
    return t


def _normalize_meeting_href(s: str) -> str:
    s = s.strip()
    s = s.replace("&amp;", "&").replace("&#38;", "&").replace("&amp;#38;", "&")
    s = s.rstrip(").,;]\"'")
    low = s.lower()
    if low.startswith("http://") and _should_upgrade_http_to_https(s):
        s = "https://" + s[7:]
    return s


def _should_upgrade_http_to_https(url: str) -> bool:
    u = (url or "").lower()
    if "zoom." in u or "teams.microsoft" in u or "teams.live" in u:
        return True
    if "meet.google" in u or "webex" in u:
        return True
    if "us0" in u and "web.zoom" in u:
        return True
    return False


def extract_urls_from_location(loc: Any) -> list[str]:

    if not loc:
        return []
    parts: list[str] = []

    def _walk(x: Any) -> None:
        if isinstance(x, str) and x.strip():
            parts.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)

    _walk(loc)
    blob = "\n".join(parts)
    return extract_https_urls(blob)


def event_meeting_urls_from_body_object(ev: dict[str, Any]) -> list[str]:

    b = ev.get("body")
    if isinstance(b, dict):
        c = b.get("content")
        if isinstance(c, str) and c.strip():
            return extract_https_urls(c)
    return extract_https_urls(event_body_text(ev))


def event_body_is_empty_for_fetch(ev: dict[str, Any]) -> bool:

    b = ev.get("body")
    if not isinstance(b, dict):
        return True
    c = b.get("content")
    return not (isinstance(c, str) and c.strip())


def body_preview_suggests_external_meeting(preview: str) -> bool:

    p = (preview or "").strip()
    if len(p) < 6:
        return False
    return bool(
        re.search(
            r"zoom|\.zoom|meet\.google|/j/\d|webex\.|us\d+web\.zoom|приглаш.*zoom",
            p,
            re.IGNORECASE,
        )
    )


def event_body_text(ev: dict[str, Any]) -> str:

    b = ev.get("body")
    if isinstance(b, dict):
        raw = b.get("content")
        if isinstance(raw, str) and raw.strip():
            if (b.get("contentType") or "").lower() == "html":
                return _strip_htmlish(raw)
            return raw
    prev = ev.get("bodyPreview")
    if isinstance(prev, str) and prev.strip():
        return prev
    return ""


def extract_https_urls(text: str) -> list[str]:
    if not text or not str(text).strip():
        return []
    raw = str(text)
    found: list[str] = []

    found += re.findall(
        r'(?:href|src)\s*=\s*"(https?://[^"]+)"', raw, flags=re.IGNORECASE
    )

    found += re.findall(
        r"(?:href|src)\s*=\s*'(https?://[^']+)'", raw, flags=re.IGNORECASE
    )

    found += _PLAIN_HTTPS_RE.findall(raw)
    out: list[str] = []
    seen: set[str] = set()
    for u in found:
        n = _normalize_meeting_href(u)
        if n.lower().startswith("http://") and _should_upgrade_http_to_https(n):
            n = "https://" + n[7:]
        n = _normalize_meeting_href(n)
        if not n.lower().startswith("https://"):
            continue
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def classify_meeting_url(url: str) -> str:
    u = (url or "").lower()

    if re.search(r"zoom\.(us|com)(/|$|\?|#|&)", u) or ".zoom." in u:
        return "zoom"
    if "teams.microsoft" in u or "teams.live" in u or "teams.google" in u:
        return "teams"
    if "meet.google" in u:
        return "meet"
    if "webex" in u:
        return "webex"
    if "gotomeeting" in u or "global.gotowebinar" in u:
        return "goto"
    if "whereby" in u or "daily.co" in u or "meet.jit.si" in u:
        return "webrtc"
    return "other"


def build_meeting_link_objects(urls: list[str]) -> list[dict[str, str]]:
    return [
        {
            "url": u,
            "kind": classify_meeting_url(u),
        }
        for u in urls
    ]
