import pytest
from fastapi import HTTPException

from application.media_access import (
    assert_media_readable,
    normalize_media_subpath,
    private_media_prefix,
)


def test_private_prefixes_detected():
    assert private_media_prefix("chat_attachments/1/2/a.bin") == "chat_attachments"
    assert private_media_prefix("expenses/abc/file.pdf") == "expenses"
    assert private_media_prefix("correspondence/d1/f.pdf") == "correspondence"
    assert private_media_prefix("vacation_leave_requests/2026/1/a.pdf") == "vacation_leave_requests"
    assert private_media_prefix("hr_personnel/3/x.jpg") == "hr_personnel"
    assert private_media_prefix("inventory/photo.jpg") is None
    assert private_media_prefix("tickets/abc_file.pdf") is None


@pytest.mark.asyncio
async def test_chat_path_denied_for_any_user():
    with pytest.raises(HTTPException) as ei:
        await assert_media_readable(
            "chat_attachments/9/1/secret.html",
            {"id": 1, "role": "Сотрудник"},
        )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_ticket_owner_allowed(monkeypatch):
    import application.media_access as media_access

    async def fake_assert(filename, user):
        assert filename == "aabb_file.pdf"
        assert user["id"] == 7

    monkeypatch.setattr(media_access, "assert_ticket_attachment_readable", fake_assert)
    await media_access.assert_media_readable(
        "tickets/aabb_file.pdf",
        {"id": 7, "role": "Сотрудник"},
    )


@pytest.mark.asyncio
async def test_ticket_full_access_role_skips_lookup():
    # Partner may download without tickets-service roundtrip.
    await assert_media_readable(
        "tickets/aabb_file.pdf",
        {"id": 99, "role": "Партнер"},
    )


def test_normalize_strips_slashes():
    assert normalize_media_subpath("/tickets/a.pdf") == "tickets/a.pdf"
