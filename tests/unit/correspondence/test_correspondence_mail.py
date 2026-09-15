from infrastructure.correspondence_mail import _build_message


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
