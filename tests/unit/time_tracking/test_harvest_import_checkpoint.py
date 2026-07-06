from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.import_harvest_time_report import (
    CHECKPOINT_VERSION,
    HarvestRow,
    _default_checkpoint_path,
    _file_fingerprint,
    _load_checkpoint,
    _project_key,
    _project_key_parts,
    _save_checkpoint,
    _sorted_project_pairs,
)


def _sample_row(client: str, project: str, n: int) -> HarvestRow:
    from datetime import date
    from decimal import Decimal

    return HarvestRow(
        source_row_number=n,
        work_date=date(2024, 1, 1),
        client_name=client,
        project_name=project,
        project_code=None,
        task_name="Task",
        notes=None,
        hours=Decimal("1"),
        is_billable=True,
        first_name="A",
        last_name="B",
        employee_id=None,
        billable_rate=Decimal("100"),
        cost_rate=Decimal("0"),
        currency="EUR",
        external_reference_url=None,
    )


def test_project_key_roundtrip() -> None:
    key = _project_key("Client A", "Project X")
    assert _project_key_parts(key) == ("Client A", "Project X")


def test_sorted_project_pairs() -> None:
    rows = [
        _sample_row("Zeta", "P2", 1),
        _sample_row("Alpha", "P1", 2),
        _sample_row("Alpha", "P2", 3),
    ]
    assert _sorted_project_pairs(rows) == [
        ("Alpha", "P1"),
        ("Alpha", "P2"),
        ("Zeta", "P2"),
    ]


def test_checkpoint_save_load(tmp_path: Path) -> None:
    csv_path = tmp_path / "report.csv"
    csv_path.write_text("date,client,project\n", encoding="utf-8")
    ckpt_path = _default_checkpoint_path(csv_path)
    assert ckpt_path.name == "report.csv.harvest-import.checkpoint.json"

    data = _load_checkpoint(ckpt_path, csv_path, reset=False)
    assert data["version"] == CHECKPOINT_VERSION
    assert data["source_file"] == "report.csv"
    assert data["completed_project_keys"] == []

    data["completed_project_keys"] = [_project_key("C", "P")]
    _save_checkpoint(ckpt_path, data)
    loaded = json.loads(ckpt_path.read_text(encoding="utf-8"))
    assert loaded["completed_project_keys"] == [_project_key("C", "P")]
    assert loaded["updated_at"]


def test_checkpoint_rejects_changed_file(tmp_path: Path) -> None:
    csv_path = tmp_path / "report.csv"
    csv_path.write_text("v1", encoding="utf-8")
    ckpt_path = _default_checkpoint_path(csv_path)
    data = _load_checkpoint(ckpt_path, csv_path)
    data["completed_project_keys"] = [_project_key("C", "P")]
    _save_checkpoint(ckpt_path, data)

    csv_path.write_text("v2", encoding="utf-8")
    with pytest.raises(SystemExit):
        _load_checkpoint(ckpt_path, csv_path)
