"""Read-only views into stored files for previews: ZIP archives and e-mails (.eml).

ZIP listing reads only the central directory, and entries are streamed in blocks, so a 2 GB archive costs
neither memory nor time. Entries are addressed by their index (names inside Windows archives are often not
UTF-8 and would not survive a round trip). Blocking I/O: call through ``run_in_threadpool`` /
``iterate_in_threadpool``.
"""

from __future__ import annotations

import mimetypes
import zipfile
from collections.abc import Callable, Iterator
from datetime import datetime
from email import policy
from email.parser import BytesParser
from typing import BinaryIO

MAX_ENTRIES = 5000
STREAM_BLOCK = 256 * 1024
MAX_EMAIL_BYTES = 30 * 1024 * 1024


def _entry_name(info: zipfile.ZipInfo) -> str:
    """Archives made by Windows Explorer store names in the OEM code page (cp866 for Russian)."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for codec in ("utf-8", "cp866"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    return info.filename


def list_zip(source: BinaryIO) -> dict:
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        return {"supported": False, "reason": "Архив повреждён или это не ZIP"}
    with archive:
        infos = archive.infolist()
        entries = []
        for index, info in enumerate(infos[:MAX_ENTRIES]):
            modified = None
            try:
                modified = datetime(*info.date_time).isoformat()
            except ValueError:
                pass
            entries.append({
                "index": index,
                "path": _entry_name(info).replace("\\", "/"),
                "dir": info.is_dir(),
                "size": info.file_size,
                "compressed": info.compress_size,
                "modified": modified,
                "encrypted": bool(info.flag_bits & 0x1),
            })
        return {
            "supported": True,
            "entries": entries,
            "total": len(infos),
            "truncated": len(infos) > MAX_ENTRIES,
            "uncompressed": sum(info.file_size for info in infos),
        }


def zip_entry(source: BinaryIO, index: int) -> tuple[str, str, int]:
    """(file name, content type, size) of an entry; raises KeyError / ValueError."""
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if index < 0 or index >= len(infos) or infos[index].is_dir():
            raise KeyError(index)
        info = infos[index]
        if info.flag_bits & 0x1:
            raise ValueError("Файл в архиве защищён паролем")
        name = _entry_name(info).replace("\\", "/").rsplit("/", 1)[-1] or "file"
        return name, mimetypes.guess_type(name)[0] or "application/octet-stream", info.file_size


def stream_zip_entry(open_source: Callable[[], BinaryIO], index: int) -> Iterator[bytes]:
    """Yield the entry in blocks (never more than its declared size — guards against zip bombs)."""
    with open_source() as source, zipfile.ZipFile(source) as archive:
        info = archive.infolist()[index]
        left = info.file_size
        with archive.open(info) as member:
            while left > 0:
                block = member.read(min(STREAM_BLOCK, left))
                if not block:
                    break
                left -= len(block)
                yield block


def _attachments(message) -> list:
    return [part for part in message.iter_attachments() if part.get_content_disposition() in {"attachment", "inline", None} and part.get_filename()]


def parse_eml(data: bytes) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(data)
    html = text = ""
    body = message.get_body(preferencelist=("html",))
    if body is not None:
        html = body.get_content()
    plain = message.get_body(preferencelist=("plain",))
    if plain is not None:
        text = plain.get_content()
    attachments = []
    for index, part in enumerate(_attachments(message)):
        payload = part.get_payload(decode=True) or b""
        attachments.append({
            "index": index,
            "name": part.get_filename() or f"attachment-{index + 1}",
            "contentType": part.get_content_type(),
            "size": len(payload),
        })
    header = lambda key: " ".join(str(message.get(key) or "").split())  # noqa: E731
    return {
        "subject": header("Subject"),
        "from": header("From"),
        "to": header("To"),
        "cc": header("Cc"),
        "date": header("Date"),
        "html": html[:2_000_000],
        "text": text[:500_000],
        "attachments": attachments,
    }


def eml_attachment(data: bytes, index: int) -> tuple[str, str, bytes]:
    message = BytesParser(policy=policy.default).parsebytes(data)
    parts = _attachments(message)
    if index < 0 or index >= len(parts):
        raise KeyError(index)
    part = parts[index]
    return part.get_filename() or f"attachment-{index + 1}", part.get_content_type(), part.get_payload(decode=True) or b""
