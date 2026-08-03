from presentation.board_import_export import (
    BoardImportError,
    board_out_to_export_dict,
    detect_and_normalize_import,
)
from presentation.board_payload import (
    BoardLabelOut,
    BoardOut,
    CardOut,
    ChecklistItemOut,
    ColumnOut,
)
from datetime import datetime, timezone


def test_normalize_trello_lists_and_cards():
    payload = {
        "name": "Work Tasks",
        "lists": [
            {"id": "l1", "name": "To Do", "pos": 1, "closed": False},
            {"id": "l2", "name": "Done", "pos": 2, "closed": True},
        ],
        "cards": [
            {
                "id": "c1",
                "idList": "l1",
                "name": "Task A",
                "desc": "Body",
                "pos": 10,
                "closed": False,
                "due": "2026-08-10T12:00:00.000Z",
                "dueComplete": False,
                "labels": [{"name": "Urgent", "color": "red"}],
            },
            {"id": "c2", "idList": "l2", "name": "Hidden", "closed": False},
        ],
        "checklists": [
            {
                "idCard": "c1",
                "checkItems": [
                    {"name": "Step 1", "state": "complete", "pos": 1},
                    {"name": "Step 2", "state": "incomplete", "pos": 2},
                ],
            }
        ],
        "labels": [{"name": "Urgent", "color": "red"}],
    }
    norm = detect_and_normalize_import(payload)
    assert norm["title"] == "Work Tasks"
    assert len(norm["columns"]) == 1
    assert norm["columns"][0]["title"] == "To Do"
    assert len(norm["columns"][0]["cards"]) == 1
    card = norm["columns"][0]["cards"][0]
    assert card["title"] == "Task A"
    assert card["body"] == "Body"
    assert card["due_at"] is not None
    assert len(card["checklist"]) == 2
    assert card["checklist"][0]["is_done"] is True
    assert any(lb["title"] == "Urgent" for lb in norm["board_labels"])


def test_normalize_native_export_roundtrip_shape():
    board = BoardOut(
        id=1,
        user_id=7,
        title="Моя доска",
        visibility="personal",
        color="#111",
        background_url=None,
        board_labels=[BoardLabelOut(id=1, title="A", color="#f00", position=0)],
        columns=[
            ColumnOut(
                id=10,
                title="Сегодня",
                position=0,
                color="#7c3aed",
                is_collapsed=False,
                task_count=1,
                cards=[
                    CardOut(
                        id=100,
                        title="Do thing",
                        body="x",
                        position=0,
                        created_at=datetime.now(timezone.utc),
                        due_at=None,
                        is_completed=False,
                        is_archived=False,
                        labels=[],
                        checklist=[
                            ChecklistItemOut(id=1, title="one", is_done=False, position=0)
                        ],
                        participant_user_ids=[],
                        attachments=[],
                        comments=[],
                    )
                ],
            )
        ],
    )
    exported = board_out_to_export_dict(board)
    assert exported["format"] == "kosta_todos"
    norm = detect_and_normalize_import(exported)
    assert norm["title"] == "Моя доска"
    assert norm["columns"][0]["cards"][0]["title"] == "Do thing"
    assert norm["columns"][0]["cards"][0]["checklist"][0]["title"] == "one"


def test_unrecognized_payload_raises():
    try:
        detect_and_normalize_import({"foo": 1})
        assert False, "expected error"
    except BoardImportError:
        pass
