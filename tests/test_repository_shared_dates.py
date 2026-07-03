

from datetime import date

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from infrastructure.repository_shared import _date_none              


def test_date_none_from_iso_string():
    assert _date_none("2023-01-23") == date(2023, 1, 23)


def test_date_none_from_date():
    d = date(2026, 5, 26)
    assert _date_none(d) is d


def test_date_none_none():
    assert _date_none(None) is None
