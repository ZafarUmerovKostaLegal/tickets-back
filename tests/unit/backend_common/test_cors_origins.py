from backend_common.cors_origins import resolve_cors_origins


def test_never_returns_star():
    origins = resolve_cors_origins(frontend_url="*", include_local_defaults=True)
    assert "*" not in origins
    assert origins


def test_frontend_url_list():
    origins = resolve_cors_origins(
        frontend_url="https://app.example.com, http://localhost:5173",
        include_local_defaults=False,
    )
    assert origins == ["https://app.example.com", "http://localhost:5173"]


def test_production_includes_known_hosts():
    origins = resolve_cors_origins(
        frontend_url="",
        environment="production",
        include_local_defaults=False,
    )
    assert "https://tickets.kostalegal.com" in origins
