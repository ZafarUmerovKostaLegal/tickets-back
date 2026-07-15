from types import SimpleNamespace

from infrastructure.azure_ad import (
    allowed_redirect_uris,
    normalize_redirect_uri,
    resolve_exchange_redirect_uri,
    acquire_token_by_code,
)


MOBILE = "msauth://com.kostalegal.tickets/QWv/YpjNHWz0oXRS6wMEogdvvn0="
WEB = "https://tickets.example.com/api/v1/auth/azure/callback"


def test_normalize_keeps_msauth_equals():
    assert normalize_redirect_uri(MOBILE) == MOBILE
    assert normalize_redirect_uri(WEB + "/") == WEB


def test_resolve_prefers_requested_when_allowlisted():
    settings = SimpleNamespace(
        auth_redirect_uri=WEB,
        auth_mobile_redirect_uri=MOBILE,
        auth_redirect_uris_extra="",
    )
    assert resolve_exchange_redirect_uri(MOBILE, settings=settings) == MOBILE
    assert resolve_exchange_redirect_uri(None, settings=settings) == WEB
    assert resolve_exchange_redirect_uri("https://evil.example/", settings=settings) is None


def test_allowed_includes_extra():
    settings = SimpleNamespace(
        auth_redirect_uri=WEB,
        auth_mobile_redirect_uri="",
        auth_redirect_uris_extra=f"{MOBILE}, https://other.example/cb/",
    )
    allow = allowed_redirect_uris(settings)
    assert MOBILE in allow
    assert "https://other.example/cb" in allow


def test_acquire_token_routes_pkce_to_public_exchange(monkeypatch):
    import infrastructure.azure_ad as azure_ad

    settings = SimpleNamespace(
        auth_redirect_uri=WEB,
        auth_mobile_redirect_uri=MOBILE,
        auth_redirect_uris_extra="",
        azure_tenant_id="tid",
        azure_client_id="cid",
        azure_client_secret="sec",
    )
    seen: dict = {}

    def fake_pkce(code, redirect_uri, code_verifier, settings_arg):
        seen["args"] = (code, redirect_uri, code_verifier)
        return {"access_token": "at", "id_token_claims": {"oid": "o1"}}

    monkeypatch.setattr(azure_ad, "get_settings", lambda: settings)
    monkeypatch.setattr(azure_ad, "_acquire_token_pkce_public", fake_pkce)
    out = azure_ad.acquire_token_by_code(
        "auth-code", redirect_uri=MOBILE, code_verifier="pkce-v"
    )
    assert out is not None
    assert out["id_token_claims"]["oid"] == "o1"
    assert seen["args"] == ("auth-code", MOBILE, "pkce-v")


def test_acquire_token_rejects_unknown_redirect(monkeypatch):
    import infrastructure.azure_ad as azure_ad

    settings = SimpleNamespace(
        auth_redirect_uri=WEB,
        auth_mobile_redirect_uri=MOBILE,
        auth_redirect_uris_extra="",
        azure_tenant_id="tid",
        azure_client_id="cid",
        azure_client_secret="sec",
    )
    monkeypatch.setattr(azure_ad, "get_settings", lambda: settings)
    assert (
        azure_ad.acquire_token_by_code("c", redirect_uri="https://evil/", code_verifier="v")
        is None
    )
