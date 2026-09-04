"""Convert Word/Office attachments to PDF via LibreOffice (soffice)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

OFFICE_SUFFIXES = {".doc", ".docx", ".odt", ".rtf"}
WORD_MIME_HINTS = (
    "word",
    "msword",
    "officedocument.wordprocessingml",
    "opendocument.text",
    "rtf",
)


def is_office_document(filename: str | None, content_type: str | None = None) -> bool:
    name = (filename or "").strip().lower()
    if Path(name).suffix in OFFICE_SUFFIXES:
        return True
    mime = (content_type or "").split(";")[0].strip().lower()
    return any(h in mime for h in WORD_MIME_HINTS)


def find_soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert_office_bytes_to_pdf(content: bytes, filename: str) -> bytes:
    """Return PDF bytes. Raises RuntimeError if LibreOffice is missing or conversion fails."""
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice не установлен (нужен soffice для преобразования в PDF)")
    ext = Path(filename or "letter.docx").suffix.lower() or ".docx"
    if ext not in OFFICE_SUFFIXES:
        ext = ".docx"
    with tempfile.TemporaryDirectory(prefix="corr-pdf-") as td:
        work = Path(td)
        src = work / f"source{ext}"
        src.write_bytes(content)
        profile = work / "lo-profile"
        profile.mkdir()
        profile_uri = profile.resolve().as_uri()
        cmd = [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--norestore",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work),
            str(src),
        ]
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                timeout=90,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("Преобразование в PDF превысило время ожидания") from e
        pdf = work / "source.pdf"
        if proc.returncode != 0 or not pdf.is_file():
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")[:800]
            _log.warning("office-to-pdf failed code=%s err=%s", proc.returncode, err)
            raise RuntimeError(err.strip() or "LibreOffice не создал PDF")
        return pdf.read_bytes()
