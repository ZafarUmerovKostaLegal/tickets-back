from backend_common.redis_url_warn import (
    redis_url_has_password,
    warn_if_redis_url_unauthenticated,
)


def test_redis_url_has_password():
    assert redis_url_has_password("redis://:secret@redis:6379/0")
    assert redis_url_has_password("redis://user:secret@redis:6379/0")
    assert not redis_url_has_password("redis://redis:6379/0")
    assert not redis_url_has_password("")
    assert not redis_url_has_password(None)


def test_warn_when_no_password(caplog):
    with caplog.at_level("WARNING"):
        warn_if_redis_url_unauthenticated("redis://redis:6379/0", service="celery")
    assert any("REDIS_URL has no password" in r.message for r in caplog.records)
    assert any("celery" in r.message for r in caplog.records)


def test_no_warn_when_password_set(caplog):
    with caplog.at_level("WARNING"):
        warn_if_redis_url_unauthenticated("redis://:s3cret@redis:6379/0", service="celery")
    assert not any("REDIS_URL has no password" in r.message for r in caplog.records)
