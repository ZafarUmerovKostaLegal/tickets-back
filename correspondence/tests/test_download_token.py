import pytest

from infrastructure.download_qr import build_public_download_path
from infrastructure.download_token import sign_download_token, verify_download_token
from infrastructure.public_card import render_public_card

DOC = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ATT = "11111111-2222-3333-4444-555555555555"
SECRET = "test-secret-correspondence-qr-32chars"


def test_sign_and_verify_download_token_roundtrip():
    token = sign_download_token(
        SECRET,
        document_id=DOC,
        attachment_id=ATT,
        ttl_seconds=3600,
    )
    assert token.startswith("1.")
    assert verify_download_token(SECRET, token=token, document_id=DOC, attachment_id=ATT) == ATT


def test_document_scoped_token_roundtrip():
    token = sign_download_token(
        SECRET,
        document_id=DOC,
        attachment_id=None,
        ttl_seconds=3600,
    )
    assert token.startswith("2.")
    assert "." in token
    # Compact tokens stay short enough for phone QR scanners.
    assert len(token) < 120
    assert verify_download_token(SECRET, token=token, document_id=DOC) is None


def test_verify_rejects_tampered_token():
    token = sign_download_token(
        SECRET,
        document_id=DOC,
        attachment_id=ATT,
        ttl_seconds=3600,
    )
    parts = token.split(".")
    parts[-1] = ("0" * 32) if parts[-1] != ("0" * 32) else ("1" * 32)
    with pytest.raises(ValueError, match="Недействительная"):
        verify_download_token(SECRET, token=".".join(parts), document_id=DOC, attachment_id=ATT)


def test_verify_rejects_wrong_document():
    token = sign_download_token(
        SECRET,
        document_id=DOC,
        attachment_id=ATT,
        ttl_seconds=3600,
    )
    other = "99999999-8888-7777-6666-555555555555"
    with pytest.raises(ValueError, match="Недействительная"):
        verify_download_token(SECRET, token=token, document_id=other, attachment_id=ATT)


def test_verify_rejects_swapped_attachment():
    token = sign_download_token(
        SECRET,
        document_id=DOC,
        attachment_id=ATT,
        ttl_seconds=3600,
    )
    other_att = "99999999-8888-7777-6666-555555555555"
    with pytest.raises(ValueError, match="Недействительная"):
        verify_download_token(SECRET, token=token, document_id=DOC, attachment_id=other_att)


def test_verify_rejects_expired_token(monkeypatch):
    import infrastructure.download_token as mod

    real_time = mod.time.time
    monkeypatch.setattr(mod.time, "time", lambda: 1_700_000_000)
    token = sign_download_token(
        SECRET,
        document_id=DOC,
        attachment_id=ATT,
        ttl_seconds=60,
    )
    monkeypatch.setattr(mod.time, "time", lambda: 1_700_000_000 + 120)
    with pytest.raises(ValueError, match="устарела"):
        verify_download_token(SECRET, token=token, document_id=DOC, attachment_id=ATT)
    monkeypatch.setattr(mod.time, "time", real_time)


def test_sign_rejects_non_uuid():
    with pytest.raises(ValueError, match="document_id"):
        sign_download_token(SECRET, document_id="not-a-uuid", attachment_id=ATT, ttl_seconds=60)


def test_document_scoped_token_rejects_wrong_document():
    token = sign_download_token(
        SECRET,
        document_id=DOC,
        ttl_seconds=3600,
    )
    other = "99999999-8888-7777-6666-555555555555"
    with pytest.raises(ValueError, match="Недействительная"):
        verify_download_token(SECRET, token=token, document_id=other)


def test_legacy_json_token_still_verifies():
    """Old body.sig tokens minted before compact format must keep working."""
    import base64
    import hashlib
    import hmac
    import json

    payload = {
        "act": "download",
        "aid": ATT,
        "did": DOC,
        "exp": 1_900_000_000,
        "n": "abcd1234abcd1234",
        "v": 1,
    }
    body_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(SECRET.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    token = f"{body_b64}.{sig}"
    assert verify_download_token(SECRET, token=token, document_id=DOC, attachment_id=ATT) == ATT


def test_qr_link_opens_card_not_the_file():
    token = sign_download_token(SECRET, document_id=DOC, ttl_seconds=3600)
    path = build_public_download_path(document_id=DOC, token=token)
    assert "/public-card?" in path
    assert "/public-file" not in path


def test_public_card_escapes_text_and_blocks_other_origins():
    page, nonce = render_public_card(
        registry_number="<script>",
        issued_on=None,
        counterparty="Фирма",
        subject="Тема",
        doc_type="letter",
        file_name="letter.pdf",
        file_path="/api/v1/correspondence/x/public-file",
    )
    assert "<script>" not in page.split("<script nonce=")[0]
    assert "&lt;script&gt;" in page
    assert f'nonce="{nonce}"' in page
    assert "connect-src" in page
