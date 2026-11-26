"""Previews: what is inside a ZIP (incl. Windows cp866 names), single entries, e-mails with attachments."""

import io
import os
import zipfile
from email.message import EmailMessage

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from test_versioning import BASE, _folder, _upload, env  # noqa: F401  (fixture)


class _WindowsName(zipfile.ZipInfo):
    """Windows Explorer writes names in the OEM code page (cp866) without the UTF-8 flag."""

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp866"), self.flag_bits & ~0x800


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Договоры/SPA.txt", "share purchase")
        archive.writestr("readme.md", "# hello")
        archive.writestr(_WindowsName("Счёт.txt"), "invoice")
    return buffer.getvalue()


def test_archive_listing_and_entries(env):
    client, _, _ = env
    _, doc = _upload(client, _folder(client, "Agreements"), "Data room.zip", _zip())
    listing = client.get(f"{BASE}/documents/{doc['id']}/archive").json()
    assert listing["supported"] and listing["total"] == 3
    paths = {entry["path"]: entry for entry in listing["entries"]}
    assert set(paths) == {"Договоры/SPA.txt", "readme.md", "Счёт.txt"}
    entry = client.get(f"{BASE}/documents/{doc['id']}/archive/{paths['Договоры/SPA.txt']['index']}", params={"inline": True})
    assert entry.status_code == 200 and entry.content == b"share purchase"
    assert entry.headers["content-disposition"].startswith("inline;") and entry.headers["content-type"].startswith("text/plain")
    assert client.get(f"{BASE}/documents/{doc['id']}/archive/99").status_code == 404

    link = client.post(f"{BASE}/documents/{doc['id']}/link", params={"inline": True, "entry": paths["readme.md"]["index"]}).json()["url"]
    assert client.get(link).content == b"# hello"

    _, broken = _upload(client, _folder(client, "Agreements"), "broken.zip", b"not a zip at all")
    assert client.get(f"{BASE}/documents/{broken['id']}/archive").json()["supported"] is False


def test_email_preview_and_attachments(env):
    client, _, _ = env
    message = EmailMessage()
    message["Subject"] = "SPA — final"
    message["From"] = "Anna <anna@buyer.com>"
    message["To"] = "lawyer@kostalegal.com"
    message.set_content("Plain body")
    message.add_alternative("<p>Final <b>SPA</b></p>", subtype="html")
    message.add_attachment(b"%PDF-1.4 test", maintype="application", subtype="pdf", filename="SPA.pdf")
    _, doc = _upload(client, _folder(client, "Agreements"), "letter.eml", bytes(message))
    preview = client.get(f"{BASE}/documents/{doc['id']}/email").json()
    assert preview["subject"] == "SPA — final" and "anna@buyer.com" in preview["from"]
    assert "<b>SPA</b>" in preview["html"] and preview["text"].strip() == "Plain body"
    assert preview["attachments"] == [{"index": 0, "name": "SPA.pdf", "contentType": "application/pdf", "size": 13, "saved": None}]
    attachment = client.get(f"{BASE}/documents/{doc['id']}/email/0")
    assert attachment.content == b"%PDF-1.4 test"
