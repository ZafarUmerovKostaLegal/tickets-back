from pathlib import Path

from backend_common.media_response import (
    blocked_upload_extension,
    media_file_response,
    resolve_media_type,
    safe_download_filename,
)


def test_blocks_html_svg_js_uploads():
    assert blocked_upload_extension("x.html") == ".html"
    assert blocked_upload_extension("pic.SVG") == ".svg"
    assert blocked_upload_extension("a.js") == ".js"
    assert blocked_upload_extension("doc.pdf") is None


def test_html_forced_to_octet_stream(tmp_path: Path):
    path = tmp_path / "evil.html"
    path.write_text("<script>alert(1)</script>", encoding="utf-8")
    assert resolve_media_type(path) == "application/octet-stream"
    resp = media_file_response(path)
    assert resp.media_type == "application/octet-stream"
    assert "attachment" in (resp.headers.get("content-disposition") or "").lower()
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_safe_filename_strips_paths():
    assert safe_download_filename(r"..\..\a.html") == "a.html"
    assert "\r" not in safe_download_filename("a\r\nb.pdf")
