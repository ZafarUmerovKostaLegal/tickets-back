from __future__ import annotations

import re
import unicodedata

MAX_INVOICE_PDF_BYTES = 25 * 1024 * 1024
PDF_MAGIC = b"%PDF-"

_UNSAFE_NAME = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


class InvoicePdfValidationError(ValueError):
    pass


def validate_invoice_pdf(content: bytes) -> None:
    """Accept only real PDF files within the size limit."""
    if not content:
        raise InvoicePdfValidationError("Пустой файл")
    if len(content) > MAX_INVOICE_PDF_BYTES:
        raise InvoicePdfValidationError(
            f"Файл больше {MAX_INVOICE_PDF_BYTES // (1024 * 1024)} МБ",
        )
    # Some generators prepend a BOM or whitespace; the header must still be within the first KB.
    if PDF_MAGIC not in content[:1024]:
        raise InvoicePdfValidationError("Это не PDF-файл")


def safe_pdf_file_name(raw: str | None, fallback: str) -> str:
    name = unicodedata.normalize("NFC", (raw or "").strip())
    name = _UNSAFE_NAME.sub("_", name).strip(" .")
    if not name:
        name = fallback
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    if len(name) > 200:
        name = f"{name[:196].rstrip(' .')}.pdf"
    return name


def content_disposition(file_name: str, disposition: str = "inline") -> str:
    """RFC 6266 header with an ASCII fallback and a UTF-8 name for Cyrillic file names."""
    from urllib.parse import quote

    ascii_name = file_name.encode("ascii", "ignore").decode("ascii").replace('"', "") or "invoice.pdf"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"
