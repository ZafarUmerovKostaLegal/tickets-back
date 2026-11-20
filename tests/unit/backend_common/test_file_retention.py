from pathlib import Path

from backend_common.file_retention import DELETED_DIR, retire_file


def test_retired_file_is_moved_aside_and_kept(tmp_path: Path):
    original = tmp_path / "receipts" / "2019" / "check.pdf"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"old receipt from 2019")

    kept = retire_file(original)

    assert kept is not None
    assert not original.exists()
    assert kept.parent == original.parent / DELETED_DIR
    assert kept.name.endswith("__check.pdf")
    assert kept.read_bytes() == b"old receipt from 2019"


def test_same_name_twice_never_overwrites(tmp_path: Path):
    first = tmp_path / "a.txt"
    first.write_text("v1")
    kept_one = retire_file(first)
    first.write_text("v2")
    kept_two = retire_file(first)
    assert kept_one != kept_two
    assert {kept_one.read_text(), kept_two.read_text()} == {"v1", "v2"}


def test_missing_paths_and_folders_are_ignored(tmp_path: Path):
    assert retire_file(None) is None
    assert retire_file(tmp_path / "nope.txt") is None
    assert retire_file(tmp_path) is None
    assert tmp_path.is_dir()
