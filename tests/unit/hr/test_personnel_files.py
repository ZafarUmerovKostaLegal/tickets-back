from pathlib import Path

import pytest
from support.service_path import ensure_service_in_path


def test_personnel_upload_rules():
    ensure_service_in_path("hr")
    from infrastructure.personnel_files import normalize_slot, validate_upload

    assert normalize_slot("Contract") == "contract"
    with pytest.raises(ValueError):
        normalize_slot("passport")
    validate_upload(filename="dogovor.pdf", content=b"%PDF", slot="contract")
    with pytest.raises(ValueError):
        validate_upload(filename="virus.exe", content=b"MZ", slot="contract")
    with pytest.raises(ValueError):
        validate_upload(filename="notes.pdf", content=b"%PDF", slot="photo")


def test_personnel_storage_stays_inside_media(tmp_path: Path):
    ensure_service_in_path("hr")
    from infrastructure.personnel_files import personnel_disk_path, personnel_storage_key, save_personnel_bytes

    key = personnel_storage_key(12, "abc123def456", "scan.jpg")
    save_personnel_bytes(tmp_path, key, b"jpeg")
    stored = personnel_disk_path(tmp_path, key)
    assert stored is not None and stored.read_bytes() == b"jpeg"
    assert personnel_disk_path(tmp_path, "../secret.txt") is None
