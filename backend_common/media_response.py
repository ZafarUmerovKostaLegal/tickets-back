"""Safe media download helpers — prevent XSS via stored HTML/SVG served as pages."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import FileResponse

# Never render these in the browser as their declared type on the API origin.
_FORCE_DOWNLOAD_EXT = frozenset(
    {
        ".html",
        ".htm",
        ".shtml",
        ".xhtml",
        ".svg",
        ".svgz",
        ".xml",
        ".xsl",
        ".xsd",
        ".js",
        ".mjs",
        ".css",
        ".htm",
        ".php",
        ".phtml",
        ".asp",
        ".aspx",
        ".jsp",
        ".cgi",
        ".hta",
        ".wsf",
        ".vbs",
        ".ps1",
        ".bat",
        ".cmd",
        ".sh",
        ".exe",
        ".dll",
        ".jar",
        ".war",
        ".scr",
    }
)

_FORCE_DOWNLOAD_MIME_PREFIXES = (
    "text/html",
    "application/xhtml",
    "image/svg",
    "text/xml",
    "application/xml",
    "application/javascript",
    "text/javascript",
    "text/css",
)

_INLINE_MIME_PREFIXES = ("image/", "video/", "audio/")
_INLINE_MIME_EXACT = frozenset({"application/pdf"})

_SAFE_FILENAME = re.compile(r"[^\w.\-]+", re.UNICODE)


def blocked_upload_extension(filename: str | None) -> str | None:
    """Return the blocked extension (lowercased) if upload must be rejected."""
    ext = (Path(filename or "").suffix or "").lower()
    if ext in _FORCE_DOWNLOAD_EXT:
        return ext
    return None


def safe_download_filename(name: str | None) -> str:
    base = Path(name or "file").name
    cleaned = _SAFE_FILENAME.sub("_", base).strip("._")[:180]
    return cleaned or "file"


def resolve_media_type(path: Path, *, declared: str | None = None) -> str:
    ext = path.suffix.lower()
    if ext in _FORCE_DOWNLOAD_EXT:
        return "application/octet-stream"
    guessed, _ = mimetypes.guess_type(path.name)
    media_type = (declared or guessed or "application/octet-stream").split(";")[0].strip().lower()
    if any(media_type.startswith(p) for p in _FORCE_DOWNLOAD_MIME_PREFIXES):
        return "application/octet-stream"
    return media_type or "application/octet-stream"


def _disposition_type(media_type: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _FORCE_DOWNLOAD_EXT:
        return "attachment"
    if media_type in _INLINE_MIME_EXACT or any(media_type.startswith(p) for p in _INLINE_MIME_PREFIXES):
        return "inline"
    return "attachment"


def media_file_response(
    path: Path,
    *,
    download_name: str | None = None,
    declared_media_type: str | None = None,
) -> FileResponse:
    filename = safe_download_filename(download_name or path.name)
    media_type = resolve_media_type(path, declared=declared_media_type)
    disposition = _disposition_type(media_type, filename)
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type=disposition,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Frame-Options": "DENY",
            # Extra hard disposition for clients that ignore content_disposition_type.
            "Content-Disposition": f"{disposition}; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
        },
    )
