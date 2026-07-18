from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from application.entry_archive_service import _report_dup_fingerprint


def _entry(
    *,
    auth_user_id: int = 42,
    work_date: date = date(2026, 6, 8),
    description: str = "Законодательство, документы и проект договора",
    hours: str = "2.633333",
):
    return SimpleNamespace(
        auth_user_id=auth_user_id,
        work_date=work_date,
        description=description,
        hours=Decimal(hours),
    )


def _task(name: str):
    return SimpleNamespace(name=name)


def test_fingerprint_same_content_different_task_card_matches():
    # Две записи с ОДИНАКОВЫМ названием задачи, но разными карточками (task_id) —
    # именно их отчёт схлопывает на экране. Отпечаток должен совпасть.
    fp1 = _report_dup_fingerprint(
        _entry(), _task("Document Review"), amount=Decimal("395.00"), currency="EUR"
    )
    fp2 = _report_dup_fingerprint(
        _entry(), _task("Document Review"), amount=Decimal("395.00"), currency="EUR"
    )
    assert fp1 == fp2


def test_fingerprint_task_name_case_and_space_insensitive():
    fp1 = _report_dup_fingerprint(
        _entry(), _task("Document Review"), amount=Decimal("395.00"), currency="EUR"
    )
    fp2 = _report_dup_fingerprint(
        _entry(), _task("  document   review "), amount=Decimal("395.00"), currency="EUR"
    )
    assert fp1 == fp2


def test_fingerprint_differs_by_note():
    fp1 = _report_dup_fingerprint(
        _entry(description="Первая заметка"),
        _task("Document Review"),
        amount=Decimal("395.00"),
        currency="EUR",
    )
    fp2 = _report_dup_fingerprint(
        _entry(description="Другая заметка"),
        _task("Document Review"),
        amount=Decimal("395.00"),
        currency="EUR",
    )
    assert fp1 != fp2


def test_fingerprint_differs_by_hours():
    fp1 = _report_dup_fingerprint(
        _entry(hours="2.633333"), _task("Review"), amount=Decimal("395.00"), currency="EUR"
    )
    fp2 = _report_dup_fingerprint(
        _entry(hours="3.000000"), _task("Review"), amount=Decimal("450.00"), currency="EUR"
    )
    assert fp1 != fp2


def test_fingerprint_differs_by_amount():
    fp1 = _report_dup_fingerprint(
        _entry(), _task("Review"), amount=Decimal("395.00"), currency="EUR"
    )
    fp2 = _report_dup_fingerprint(
        _entry(), _task("Review"), amount=Decimal("394.50"), currency="EUR"
    )
    assert fp1 != fp2


def test_fingerprint_differs_by_user():
    fp1 = _report_dup_fingerprint(
        _entry(auth_user_id=1), _task("Review"), amount=Decimal("100.00"), currency="EUR"
    )
    fp2 = _report_dup_fingerprint(
        _entry(auth_user_id=2), _task("Review"), amount=Decimal("100.00"), currency="EUR"
    )
    assert fp1 != fp2
