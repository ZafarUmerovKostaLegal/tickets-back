from gateway.infrastructure.database_targets import DatabaseMonitorSettings, database_targets


def test_database_targets_includes_correspondence(monkeypatch):
    monkeypatch.setenv("AUTH_DATABASE_URL", "postgresql://u:p@users_db:5432/kosta_users")
    monkeypatch.setenv("CORRESPONDENCE_DATABASE_URL", "postgresql://u:p@correspondence_db:5432/kosta_correspondence")
    settings = DatabaseMonitorSettings()
    names = [name for name, _ in database_targets(settings)]
    assert "users" in names
    assert "correspondence" in names
