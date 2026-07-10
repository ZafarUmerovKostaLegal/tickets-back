from pathlib import Path

from backend_common.media_path import is_path_under_media, safe_media_path


def test_safe_media_path_accepts_nested_key(tmp_path: Path):
    base = tmp_path / "media"
    base.mkdir()
    got = safe_media_path(base, "chat/1/file.bin")
    assert got == (base / "chat" / "1" / "file.bin").resolve()


def test_safe_media_path_rejects_traversal(tmp_path: Path):
    base = tmp_path / "media"
    base.mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    assert safe_media_path(base, "../secret.txt") is None
    assert safe_media_path(base, "a/../../secret.txt") is None


def test_safe_media_path_rejects_prefix_sibling(tmp_path: Path):
    """Classic startswith bug: /app/media vs /app/media_evil."""
    media = tmp_path / "media"
    media.mkdir()
    evil = tmp_path / "media_evil"
    evil.mkdir()
    (evil / "leak.txt").write_text("x", encoding="utf-8")
    # Relative key that resolves outside via crafted join is blocked by resolve+is_relative_to
    assert safe_media_path(media, "../media_evil/leak.txt") is None
    assert not is_path_under_media(media, evil / "leak.txt")


def test_safe_media_path_rejects_absolute_and_empty(tmp_path: Path):
    base = tmp_path / "media"
    base.mkdir()
    assert safe_media_path(base, None) is None
    assert safe_media_path(base, "") is None
    assert safe_media_path(base, "   ") is None
    assert safe_media_path(base, str(base / "x")) is None
