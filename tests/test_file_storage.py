

import pytest


TICKETS_ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".doc", ".docx"}
INVENTORY_ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
NOTIFICATIONS_ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _validate_ticket_extension(filename: str) -> bool:
    ext = (filename.split(".")[-1] if "." in filename else "").lower()
    return f".{ext}" in TICKETS_ALLOWED if ext else False


def _validate_inventory_extension(filename: str) -> bool:
    ext = (filename.split(".")[-1] if "." in filename else "").lower()
    return f".{ext}" in INVENTORY_ALLOWED if ext else False


def _validate_notifications_extension(filename: str) -> bool:
    ext = (filename.split(".")[-1] if "." in filename else "").lower()
    return f".{ext}" in NOTIFICATIONS_ALLOWED if ext else False


class TestTicketsFileStorage:


    def test_validate_attachment_allowed_extension(self):

        assert _validate_ticket_extension("test.jpg") is True
        assert _validate_ticket_extension("test.pdf") is True
        assert _validate_ticket_extension("test.PNG") is True
        assert _validate_ticket_extension("test.docx") is True

    def test_validate_attachment_forbidden_extension(self):

        assert _validate_ticket_extension("test.exe") is False
        assert _validate_ticket_extension("test.php") is False
        assert _validate_ticket_extension("test.js") is False


class TestInventoryFileStorage:


    def test_validate_photo_allowed(self):

        assert _validate_inventory_extension("photo.jpg") is True
        assert _validate_inventory_extension("photo.png") is True

    def test_validate_photo_forbidden(self):

        assert _validate_inventory_extension("file.pdf") is False


class TestNotificationsFileStorage:


    def test_validate_photo_allowed(self):

        assert _validate_notifications_extension("photo.webp") is True

    def test_validate_photo_forbidden(self):

        assert _validate_notifications_extension("script.js") is False
