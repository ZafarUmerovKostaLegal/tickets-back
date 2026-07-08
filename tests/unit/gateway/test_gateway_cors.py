

from infrastructure.config import Settings, get_settings
from presentation.api import _cors_origin_regex, _cors_origins


def test_production_cors_includes_tickets_frontend(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "")
    monkeypatch.setenv("CORS_ALLOW_PRIVATE_NETWORK", "false")
    get_settings.cache_clear()
    try:
        origins = _cors_origins()
        assert "https://tickets.kostalegal.com" in origins
    finally:
        get_settings.cache_clear()


def test_production_cors_regex_matches_kostalegal_hosts():
    settings = Settings(
        ENVIRONMENT="production",
        FRONTEND_URL="",
        CORS_ALLOW_PRIVATE_NETWORK=False,
    )
    regex = _cors_origin_regex(settings)
    assert regex is not None
    import re

    assert re.match(regex, "https://tickets.kostalegal.com")
    assert re.match(regex, "https://www.tickets.kostalegal.com")


def test_development_cors_allows_private_lan_when_enabled():
    settings = Settings(
        ENVIRONMENT="development",
        CORS_ALLOW_PRIVATE_NETWORK=True,
    )
    regex = _cors_origin_regex(settings)
    assert regex is not None
    import re

    assert re.match(regex, "http://192.168.1.10:5173")


def test_production_gateway_base_url_upgraded_to_https():
    settings = Settings(
        ENVIRONMENT="production",
        GATEWAY_BASE_URL="http://ticketsback.kostalegal.com",
    )
    assert settings.gateway_base_url == "https://ticketsback.kostalegal.com"
