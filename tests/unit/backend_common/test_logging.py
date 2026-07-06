from __future__ import annotations

import logging

import pytest

from backend_common.logging import _normalize_level, configure_logging


@pytest.mark.unit
def test_normalize_level_defaults_to_info():
    assert _normalize_level(None) == logging.INFO
    assert _normalize_level("debug") == logging.DEBUG
    assert _normalize_level("unknown") == logging.INFO


@pytest.mark.unit
def test_configure_logging_sets_service_logger():
    configure_logging("test-service")
    assert logging.getLogger("test-service").isEnabledFor(logging.INFO)
