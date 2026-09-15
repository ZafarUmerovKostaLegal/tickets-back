import os

from infrastructure.config import Settings, get_settings
from infrastructure.correspondence_mail import _build_message, smtp_ready, smtp_status_summary


def test_build_message_incoming_includes_subject_and_registry():
    subject, text, html = _build_message(
        kind="incoming",
        subject_line="Претензия от клиента",
        counterparty="ООО проверка",
        registry_number="BX-2026/0005",
        reject_comment=None,
        open_url="https://app.example/correspondence",
    )
    assert subject == "Новое входящее: BX-2026/0005"
    assert "BX-2026/0005" in text
    assert "входящее письмо" in text.lower()
    assert "https://app.example/correspondence" in text
    assert "Новое входящее письмо" in html


def test_settings_fills_empty_correspondence_smtp_from_expense(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("CORRESPONDENCE_SMTP_HOST", "")
    monkeypatch.setenv("CORRESPONDENCE_SMTP_USER", "")
    monkeypatch.setenv("CORRESPONDENCE_SMTP_PASSWORD", "")
    monkeypatch.setenv("EXPENSE_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EXPENSE_SMTP_USER", "mailer@example.com")
    monkeypatch.setenv("EXPENSE_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("EXPENSE_MAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example")
    s = Settings()
    assert s.smtp_host == "smtp.example.com"
    assert s.smtp_user == "mailer@example.com"
    assert s.smtp_password == "secret"
    assert s.mail_from == "noreply@example.com"
    assert s.public_app_url == "https://app.example"
    assert smtp_ready(s) is True
    assert "host=set(smtp.example.com)" in smtp_status_summary(s)
    get_settings.cache_clear()
