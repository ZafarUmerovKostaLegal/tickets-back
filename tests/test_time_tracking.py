

from pathlib import Path

import pytest

_root = Path(__file__).resolve().parent.parent


@pytest.mark.skip(reason="Требует PostgreSQL")
async def test_time_tracking_health(time_tracking_client):

    r = await time_tracking_client.get("/health")
    assert r.status_code in (200, 503)


def test_time_tracking_repository_facade_exports_split_modules():

    repositories_facade = (_root / "time_tracking" / "infrastructure" / "repositories.py").read_text(encoding="utf-8")

    assert "from infrastructure.repository_entries import TimeEntryRepository" in repositories_facade
    assert "from infrastructure.repository_clients import (" in repositories_facade
