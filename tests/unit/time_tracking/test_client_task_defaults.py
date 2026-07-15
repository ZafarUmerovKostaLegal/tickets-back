from __future__ import annotations

import pytest

from application.client_task_defaults import DEFAULT_PROJECT_TASK_SEED


@pytest.mark.unit
def test_default_task_seed_includes_billable_and_non_billable():
    billable = [name for name, b, *_rest in DEFAULT_PROJECT_TASK_SEED if b]
    non_billable = [name for name, b, *_rest in DEFAULT_PROJECT_TASK_SEED if not b]
    assert "Research" in billable
    assert "Accounting" in non_billable
    assert len(DEFAULT_PROJECT_TASK_SEED) >= 10
    flat = [row for row in DEFAULT_PROJECT_TASK_SEED if row[2] == "flat_fee"]
    assert flat
