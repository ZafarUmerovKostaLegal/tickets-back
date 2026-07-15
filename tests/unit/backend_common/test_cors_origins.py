from backend_common.cors_origins import resolve_cors_origins


def test_returns_star():
    assert resolve_cors_origins() == ["*"]
    assert resolve_cors_origins(frontend_url="https://tickets.kostalegal.com") == ["*"]
    assert resolve_cors_origins(environment="production", include_local_defaults=False) == ["*"]
