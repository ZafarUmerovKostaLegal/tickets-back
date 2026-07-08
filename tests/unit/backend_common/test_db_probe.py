from backend_common.db_probe import (
    classify_postgres_load,
    format_bytes,
    redact_database_url,
)


def test_redact_postgresql_url():
    url = "postgresql://user:secret@db-host:5432/kosta_users"
    assert redact_database_url(url) == "postgresql://***@db-host:5432/kosta_users"


def test_redact_redis_url():
    url = "redis://:pass@redis:6379/0"
    assert redact_database_url(url) == "redis://***@redis:6379/0"


def test_classify_postgres_load_high_by_connections():
    assert classify_postgres_load(connections=80, max_connections=100, active_queries=1) == "high"


def test_classify_postgres_load_high_by_active_queries():
    assert classify_postgres_load(connections=5, max_connections=100, active_queries=12) == "high"


def test_classify_postgres_load_moderate():
    assert classify_postgres_load(connections=50, max_connections=100, active_queries=2) == "moderate"


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536) == "1.5 KB"
