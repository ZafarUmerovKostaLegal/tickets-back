"""Names and formats of Teams meeting files put into a matter."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_CUE_TIME = re.compile(r"^(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->")
_VOICE = re.compile(r"<v(?:\.[^ >]*)?\s+([^>]*)>(.*?)(?:</v>|$)", re.S)
_TAG = re.compile(r"<[^>]+>")

# Tashkent: the firm's local time for file names.
LOCAL_TZ = timezone(timedelta(hours=5))


def _title(topic: str) -> str:
    return " ".join(_UNSAFE.sub(" ", topic or "").split())[:120] or "Встреча Teams"


def _stamp(when: datetime | None) -> str:
    if when is None:
        return ""
    aware = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return aware.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H-%M")


def recording_file_name(topic: str, when: datetime | None) -> str:
    """`2026-11-22 14-30 — Запись — Acme call.mp4`"""
    parts = [part for part in (_stamp(when), "Запись", _title(topic)) if part]
    return f"{' — '.join(parts).strip(' .')}.mp4"


def transcript_file_name(topic: str, when: datetime | None) -> str:
    parts = [part for part in (_stamp(when), "Расшифровка", _title(topic)) if part]
    return f"{' — '.join(parts).strip(' .')}.txt"


def _clock(raw: str) -> str:
    stamp = raw.split("-->")[0].strip().replace(",", ".")
    pieces = stamp.split(":")
    if len(pieces) == 2:
        pieces.insert(0, "0")
    hours, minutes, seconds = pieces
    return f"{int(hours):02d}:{int(minutes):02d}:{int(float(seconds)):02d}"


def _cues(vtt: str):
    """(clock, text lines) per cue; the cue id line before the timing line is skipped."""
    for block in re.split(r"\n\s*\n", (vtt or "").replace("\r\n", "\n")):
        rows = [row.strip() for row in block.split("\n") if row.strip()]
        timing = next((i for i, row in enumerate(rows) if _CUE_TIME.match(row)), None)
        if timing is not None:
            yield _clock(rows[timing]), rows[timing + 1:]


def vtt_to_text(vtt: str, topic: str = "") -> str:
    """WebVTT from Teams → `[00:01:05] Ivan Petrov: text` lines, consecutive lines of one speaker merged."""
    lines: list[tuple[str, str, str]] = []
    for clock, rows in _cues(vtt):
        for line in rows:
            match = _VOICE.search(line)
            speaker, text = (match.group(1).strip(), match.group(2)) if match else ("", line)
            text = " ".join(html.unescape(_TAG.sub("", text)).split())
            if not text:
                continue
            speaker = html.unescape(speaker)
            if lines and lines[-1][1] == speaker:
                lines[-1] = (lines[-1][0], speaker, f"{lines[-1][2]} {text}")
            else:
                lines.append((clock, speaker, text))
    head = [f"Расшифровка встречи: {topic.strip()}", ""] if topic.strip() else []
    body = [f"[{when}] {who}: {text}" if who else f"[{when}] {text}" for when, who, text in lines]
    return "\n".join([*head, *body]).strip() + "\n"
