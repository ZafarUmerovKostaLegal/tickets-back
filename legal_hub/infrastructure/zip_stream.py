"""ZIP archives written as a stream: a few megabytes of memory whatever the size of the archive.

Entries carry data descriptors (sizes after the data), so nothing is seeked back; ZIP64 is used
for big entries and big archives. Already compressed formats are deflated at the fastest level.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool

CHUNK = 1024 * 1024
# Formats that are already compressed: deflating them again only burns CPU.
PACKED_SUFFIXES = frozenset({
    ".pdf", ".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".odt", ".ods", ".odp",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".m4a",
    ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".msg",
})
SKIPPED_NAME = "_не_скачано.txt"


@dataclass
class ZipItem:
    name: str
    size: int = 0
    path: Path | None = None
    legacy_id: str | None = None
    stamp: datetime | None = None
    is_dir: bool = False


class _Sink(io.RawIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[bytes] = []
        self.position = 0

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:  # type: ignore[override]
        block = bytes(data)
        self.chunks.append(block)
        self.position += len(block)
        return len(block)

    def tell(self) -> int:
        return self.position

    def flush(self) -> None:
        return None

    def drain(self) -> bytes:
        data = b"".join(self.chunks)
        self.chunks.clear()
        return data


def _info(item: ZipItem) -> zipfile.ZipInfo:
    stamp = item.stamp or datetime(1980, 1, 1)
    date_time = (max(stamp.year, 1980), stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)
    name = item.name.rstrip("/") + "/" if item.is_dir else item.name
    info = zipfile.ZipInfo(name, date_time=date_time)
    if item.is_dir:
        info.external_attr = 0o40775 << 16 | 0x10
        return info
    info.external_attr = 0o644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    level = 1 if Path(item.name).suffix.lower() in PACKED_SUFFIXES else 6
    if hasattr(info, "compress_level"):
        info.compress_level = level
    else:
        info._compresslevel = level  # type: ignore[attr-defined]
    return info


async def stream_zip(
    items: Iterable[ZipItem],
    load_legacy: Callable[[str], Awaitable[bytes | None]],
) -> AsyncIterator[bytes]:
    """Yield the archive piece by piece. Files that vanished are listed in ``_не_скачано.txt``."""
    sink = _Sink()
    archive = zipfile.ZipFile(sink, "w", allowZip64=True)
    skipped: list[str] = []
    try:
        for item in items:
            info = _info(item)
            if item.is_dir:
                archive.writestr(info, b"")
            elif item.path is not None:
                if not item.path.is_file():
                    skipped.append(item.name)
                    continue
                big = item.size >= zipfile.ZIP64_LIMIT // 2
                try:
                    source = await run_in_threadpool(item.path.open, "rb")
                except OSError:
                    skipped.append(item.name)
                    continue
                try:
                    target = await run_in_threadpool(archive.open, info, "w", force_zip64=big)
                    try:
                        while True:
                            block = await run_in_threadpool(source.read, CHUNK)
                            if not block:
                                break
                            await run_in_threadpool(target.write, block)
                            out = sink.drain()
                            if out:
                                yield out
                    finally:
                        await run_in_threadpool(target.close)
                except OSError:
                    skipped.append(item.name)
                finally:
                    await run_in_threadpool(source.close)
            else:
                data = await load_legacy(item.legacy_id or "") if item.legacy_id else None
                if data is None:
                    skipped.append(item.name)
                    continue
                await run_in_threadpool(archive.writestr, info, data)
            block = sink.drain()
            if block:
                yield block
        if skipped:
            note = "Эти файлы не найдены в хранилище и не попали в архив:\n" + "\n".join(skipped) + "\n"
            archive.writestr(_info(ZipItem(name=SKIPPED_NAME)), note.encode("utf-8"))
    finally:
        try:
            archive.close()
        except (ValueError, OSError):
            # The download was cut off mid-entry: nothing more to send, just let go of the archive.
            pass
    tail = sink.drain()
    if tail:
        yield tail
