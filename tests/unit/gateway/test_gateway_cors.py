from infrastructure.config import Settings, get_settings
from presentation.api import _cors_origin_regex, _cors_origins


def test_cors_origins_are_wildcard():
    get_settings.cache_clear()
    try:
        assert _cors_origins() == ["*"]
    finally:
        get_settings.cache_clear()


def test_cors_origin_regex_disabled_with_wildcard():
    settings = Settings(
        ENVIRONMENT="production",
        FRONTEND_URL="",
        CORS_ALLOW_PRIVATE_NETWORK=False,
    )
    assert _cors_origin_regex(settings) is None


def test_production_gateway_base_url_upgraded_to_https():
    settings = Settings(
        ENVIRONMENT="production",
        GATEWAY_BASE_URL="http://ticketsback.kostalegal.com",
    )
    assert settings.gateway_base_url == "https://ticketsback.kostalegal.com"
