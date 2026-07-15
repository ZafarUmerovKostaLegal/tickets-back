from backend_common.cors_origins import resolve_cors_origins


def test_never_returns_star():
    origins = resolve_cors_origins(frontend_url="*", include_local_defaults=True)
    assert "*" not in origins
    assert "https://tickets.kostalegal.com" in origins


def test_frontend_url_list():
    origins = resolve_cors_origins(
        frontend_url="https://app.example.com, http://localhost:5173",
        include_local_defaults=False,
    )
    assert origins[0] == "https://app.example.com"
    assert "http://localhost:5173" in origins
    assert "https://tickets.kostalegal.com" in origins


def test_always_includes_known_hosts():
    origins = resolve_cors_origins(
        frontend_url="",
        environment="development",
        include_local_defaults=False,
    )
    assert "https://tickets.kostalegal.com" in origins
