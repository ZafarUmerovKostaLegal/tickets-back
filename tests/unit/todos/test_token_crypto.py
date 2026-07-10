from cryptography.fernet import Fernet

from infrastructure.config import get_settings
from infrastructure.token_crypto import (
    decrypt_token,
    encrypt_token,
    encryption_enabled,
    is_encrypted_token,
    warn_if_outlook_fernet_key_empty,
)


def test_plaintext_roundtrip_without_key(monkeypatch):
    monkeypatch.setenv("OUTLOOK_TOKEN_FERNET_KEY", "")
    get_settings.cache_clear()
    assert not encryption_enabled()
    assert encrypt_token("abc") == "abc"
    assert decrypt_token("abc") == "abc"
    assert not is_encrypted_token("abc")
    get_settings.cache_clear()


def test_encrypt_decrypt_with_key(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("OUTLOOK_TOKEN_FERNET_KEY", key)
    get_settings.cache_clear()
    assert encryption_enabled()
    enc = encrypt_token("secret-token")
    assert enc.startswith("fernet:v1:")
    assert is_encrypted_token(enc)
    assert enc != "secret-token"
    assert decrypt_token(enc) == "secret-token"
    # Legacy plaintext still readable
    assert decrypt_token("legacy-plain") == "legacy-plain"
    # Idempotent encrypt
    assert encrypt_token(enc) == enc
    get_settings.cache_clear()


def test_warn_when_key_empty(caplog, monkeypatch):
    monkeypatch.setenv("OUTLOOK_TOKEN_FERNET_KEY", "")
    get_settings.cache_clear()
    with caplog.at_level("WARNING"):
        warn_if_outlook_fernet_key_empty("", service="todos")
    assert any("OUTLOOK_TOKEN_FERNET_KEY is empty" in r.message for r in caplog.records)
    get_settings.cache_clear()
