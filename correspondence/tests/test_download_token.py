import pytest

from infrastructure.download_token import sign_download_token, verify_download_token

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
    assert "." in token
    verify_download_token(SECRET, token=token, document_id=DOC, attachment_id=ATT)


def test_verify_rejects_tampered_token():
    token = sign_download_token(
        SECRET,
        document_id=DOC,
        attachment_id=ATT,
        ttl_seconds=3600,
    )
    body, sig = token.split(".", 1)
    bad_sig = ("0" * 64) if sig != ("0" * 64) else ("1" * 64)
    with pytest.raises(ValueError, match="Недействительная"):
        verify_download_token(SECRET, token=f"{body}.{bad_sig}", document_id=DOC, attachment_id=ATT)


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
    # Freeze "now" while signing with short TTL, then jump past expiry.
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
