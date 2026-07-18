from application.note_normalize import (
    normalize_note_for_duplicate_key,
    notes_are_near_duplicate,
    strip_task_prefix_from_note,
)


def test_strips_glued_document_review_prefix() -> None:
    raw = "Document ReviewЗаконодательство, документы и проект,"
    assert strip_task_prefix_from_note(raw) == "Законодательство, документы и проект,"
    assert normalize_note_for_duplicate_key(raw).startswith("законодательство")


def test_near_duplicate_truncated_notes() -> None:
    a = normalize_note_for_duplicate_key(
        "Document ReviewЗаконодательство, документы и проект,"
    )
    b = normalize_note_for_duplicate_key(
        "Законодательство, документы и проект договора на услуги"
    )
    assert notes_are_near_duplicate(a, b)


def test_does_not_strip_reviewing_continuation() -> None:
    assert strip_task_prefix_from_note("Document Reviewing files") == "Document Reviewing files"
