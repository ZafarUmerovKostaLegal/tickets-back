from backend_common.ws_secret_warn import warn_if_ws_internal_secret_empty


def test_warn_when_empty(caplog):
    with caplog.at_level("WARNING"):
        warn_if_ws_internal_secret_empty("", service="testsvc")
    assert any("WS_INTERNAL_SECRET is empty" in r.message for r in caplog.records)
    assert any("testsvc" in r.message for r in caplog.records)


def test_no_warn_when_set(caplog):
    with caplog.at_level("WARNING"):
        warn_if_ws_internal_secret_empty("secret", service="testsvc")
    assert not any("WS_INTERNAL_SECRET is empty" in r.message for r in caplog.records)
