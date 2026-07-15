import os

from application.retention_dry_run import _dry_run_enabled, _retention_days


def test_retention_days_default(monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    assert _retention_days() == 365


def test_retention_days_clamped(monkeypatch):
    monkeypatch.setenv("RETENTION_DAYS", "0")
    assert _retention_days() == 1
    monkeypatch.setenv("RETENTION_DAYS", "99999")
    assert _retention_days() == 3650
    monkeypatch.setenv("RETENTION_DAYS", "bad")
    assert _retention_days() == 365


def test_dry_run_enabled_flags(monkeypatch):
    monkeypatch.delenv("RETENTION_DRY_RUN", raising=False)
    assert _dry_run_enabled() is True
    monkeypatch.setenv("RETENTION_DRY_RUN", "0")
    assert _dry_run_enabled() is False
    monkeypatch.setenv("RETENTION_DRY_RUN", "false")
    assert _dry_run_enabled() is False
    monkeypatch.setenv("RETENTION_DRY_RUN", "1")
    assert _dry_run_enabled() is True
