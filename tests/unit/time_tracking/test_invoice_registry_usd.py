from __future__ import annotations

import pytest
from fastapi import HTTPException

from support.service_path import ensure_service_in_path


def test_usd_snapshot_rejects_bad_amounts():
    ensure_service_in_path("time_tracking")
    from presentation.routes.invoice_registry import RegistryUsdItem, clean_registry_usd_item

    saved = clean_registry_usd_item(RegistryUsdItem(
        id="sys-11111111-1111-1111-1111-111111111111",
        amountUsd="1,000.00",
        basis="1000|EUR|2026-10-02",
    ))
    assert saved == {
        "row_id": "sys-11111111-1111-1111-1111-111111111111",
        "amount_usd": "1,000.00",
        "basis": "1000|EUR|2026-10-02",
    }
    with_rate = clean_registry_usd_item(RegistryUsdItem(
        id="row1",
        amountUsd="130.00",
        basis="120|EUR|2026-10-02|0.92307692",
    ))
    assert with_rate["basis"] == "120|EUR|2026-10-02|0.92307692"
    with pytest.raises(HTTPException):
        clean_registry_usd_item(RegistryUsdItem(id="../secret", amountUsd="1.00", basis="1|USD|-"))
    with pytest.raises(HTTPException):
        clean_registry_usd_item(RegistryUsdItem(id="row1", amountUsd="1.00; drop table", basis="1|USD|-"))
    with pytest.raises(HTTPException):
        clean_registry_usd_item(RegistryUsdItem(id="row1", amountUsd="1.00", basis="1|USD|2026-10-02; delete"))
