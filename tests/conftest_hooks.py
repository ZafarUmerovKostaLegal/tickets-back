from __future__ import annotations

import hashlib
import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("E2E_FULL") == "1":
        return
    marker = config.getoption("-m", default="")
    if marker and "e2e_full" in marker:
        return

    skip = pytest.mark.skip(reason="PR sample: 25% bucket (set E2E_FULL=1 for all)")
    for item in items:
        if "test_gateway_route_requires_auth" not in item.name:
            continue
        spec = getattr(getattr(item, "callspec", None), "params", {}).get("spec")
        if spec is None:
            continue
        key = f"{spec.method}:{spec.path}"
        bucket = int(hashlib.md5(key.encode()).hexdigest(), 16) % 4
        if bucket != 0:
            item.add_marker(skip)


def pytest_itemcollected(item):
    nodeid = item.nodeid.replace("\\", "/")
    if "/tests/unit/" in nodeid:
        item.add_marker(pytest.mark.unit)
    elif "/tests/integration/" in nodeid:
        item.add_marker(pytest.mark.integration)
    elif "/tests/e2e/workflows/" in nodeid:
        item.add_marker(pytest.mark.workflow)
