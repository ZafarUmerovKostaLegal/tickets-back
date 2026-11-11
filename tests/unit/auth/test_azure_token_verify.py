from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import infrastructure.azure_ad as azure_ad
from infrastructure.azure_ad import AzureTokenExchangeError


TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
OID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


def _settings():
    return SimpleNamespace(azure_tenant_id=TENANT, azure_client_id=CLIENT)


def _mint(private_key, *, aud, tid=TENANT, iss=None, oid=OID, azp=CLIENT, extra=None):
    now = datetime.now(timezone.utc)
    payload = {
        "oid": oid,
        "sub": oid,
        "tid": tid,
        "iss": iss or f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": aud,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(hours=1),
        "preferred_username": "user@kostalegal.com",
        "name": "Test User",
    }
    if azp is not None:
        payload["azp"] = azp
    if extra:
        payload.update(extra)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key"})


class _FakeSigningKey:
    def __init__(self, public_key):
        self.key = public_key


def _patch_jwks(monkeypatch, public_key):
    monkeypatch.setattr(azure_ad, "get_settings", _settings)
    monkeypatch.setattr(
        azure_ad,
        "_get_jwks_client",
        lambda _tid: SimpleNamespace(
            get_signing_key_from_jwt=lambda _t: _FakeSigningKey(public_key)
        ),
    )


def test_verify_id_token_accepts_valid(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _patch_jwks(monkeypatch, public_key)
    token = _mint(private_key, aud=CLIENT)
    claims = azure_ad.verify_azure_id_token(token, settings=_settings())
    assert claims["oid"] == OID
    assert claims["tid"] == TENANT


def test_verify_id_token_rejects_wrong_audience(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _patch_jwks(monkeypatch, public_key)
    token = _mint(private_key, aud="someone-else-app")
    with pytest.raises(AzureTokenExchangeError):
        azure_ad.verify_azure_id_token(token, settings=_settings())


def test_verify_id_token_rejects_wrong_tenant(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _patch_jwks(monkeypatch, public_key)
    other = "99999999-9999-9999-9999-999999999999"
    token = _mint(
        private_key,
        aud=CLIENT,
        tid=other,
        iss=f"https://login.microsoftonline.com/{other}/v2.0",
    )
    with pytest.raises(AzureTokenExchangeError):
        azure_ad.verify_azure_id_token(token, settings=_settings())


def test_verify_id_token_rejects_forged_unsigned_payload(monkeypatch):
    monkeypatch.setattr(azure_ad, "get_settings", _settings)
    forged = jwt.encode(
        {
            "oid": "admin-victim-oid",
            "tid": TENANT,
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": CLIENT,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        key="not-microsoft",
        algorithm="HS256",
    )
    with pytest.raises(AzureTokenExchangeError):
        azure_ad.verify_azure_id_token(forged, settings=_settings())


def test_verify_access_token_accepts_graph_audience(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _patch_jwks(monkeypatch, public_key)
    token = _mint(private_key, aud="https://graph.microsoft.com")
    claims = azure_ad.verify_azure_access_token(token, settings=_settings())
    assert claims["oid"] == OID


def test_verify_access_token_rejects_wrong_app(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _patch_jwks(monkeypatch, public_key)
    token = _mint(private_key, aud="https://graph.microsoft.com", azp="other-client")
    with pytest.raises(AzureTokenExchangeError, match="application mismatch"):
        azure_ad.verify_azure_access_token(token, settings=_settings())


def test_unverified_helper_no_longer_accepts_unsigned(monkeypatch):
    monkeypatch.setattr(azure_ad, "get_settings", _settings)
    forged = jwt.encode(
        {"oid": "x", "aud": CLIENT, "tid": TENANT},
        key="x",
        algorithm="HS256",
        headers={"alg": "HS256"},
    )
    assert azure_ad._claims_from_id_token(forged) is None