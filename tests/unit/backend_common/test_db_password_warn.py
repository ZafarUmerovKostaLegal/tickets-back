from backend_common.db_password_warn import warn_if_database_url_uses_default_password


def test_warns_on_default_pg_password(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        warn_if_database_url_uses_default_password(
            "postgresql+asyncpg://tt:time_tracking@db:5432/tt",
            service="time_tracking",
        )
    assert any("default/local password" in r.message for r in caplog.records)


def test_warns_on_gateway_default_password(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        warn_if_database_url_uses_default_password(
            "postgresql+asyncpg://gateway:gateway@users_db:5432/kosta_users",
            service="auth",
        )
    assert any("default/local password" in r.message for r in caplog.records)


def test_no_warn_on_strong_password(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        warn_if_database_url_uses_default_password(
            "postgresql+asyncpg://tt:S3cure!Unique@db:5432/tt",
            service="time_tracking",
        )
    assert not any("default/local password" in r.message for r in caplog.records)
