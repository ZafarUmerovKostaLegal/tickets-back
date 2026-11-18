import asyncio
from types import SimpleNamespace

import pytest

from support.service_path import ensure_service_in_path


def _patch(inv, **kwargs):
    ensure_service_in_path("time_tracking")
    import application.invoice_service as svc

    async def _no_audit(*_a, **_k):
        return None

    original = svc._audit
    svc._audit = _no_audit
    try:
        return asyncio.run(svc.patch_invoice_draft(None, inv, actor_auth_user_id=1, **kwargs))
    finally:
        svc._audit = original


def _inv(status="sent", note="старое"):
    return SimpleNamespace(id="inv-1", status=status, internal_note=note, client_note=None, updated_at=None)


@pytest.mark.parametrize("status", ["draft", "sent", "partially_paid", "paid"])
def test_internal_note_editable_in_any_status(status):
    inv = _patch(_inv(status), internal_note="Строка 1\nСтрока 2  ", internal_note_provided=True)
    assert inv.internal_note == "Строка 1\nСтрока 2"


def test_internal_note_cleared_with_null():
    inv = _patch(_inv("paid"), internal_note=None, internal_note_provided=True)
    assert inv.internal_note is None


def test_internal_note_untouched_when_not_provided():
    inv = _patch(_inv("sent"), client_note="для партнёра", client_note_provided=True)
    assert inv.internal_note == "старое"
    assert inv.client_note == "для партнёра"


def test_invoice_content_still_requires_draft():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _patch(_inv("sent"), internal_note="x", internal_note_provided=True, invoice_number="A-1")


def test_registry_details_keep_full_multiline_note():
    ensure_service_in_path("time_tracking")
    from infrastructure.repository_invoice_registry import _short_registry_details

    long_note = "Первая строка\r\nВторая строка " + "x" * 400
    assert _short_registry_details(long_note) == long_note.replace("\r\n", "\n")
