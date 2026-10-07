from __future__ import annotations

import pytest

from infrastructure.database import is_transient_database_error


@pytest.mark.unit
def test_postgres_still_starting_is_transient():
    exc = Exception("CannotConnectNowError: the database system is starting up")
    assert is_transient_database_error(exc) is True


@pytest.mark.unit
def test_wrong_password_is_not_retried():
    exc = Exception("InvalidPasswordError: password authentication failed")
    assert is_transient_database_error(exc) is False
