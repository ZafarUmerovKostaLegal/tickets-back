from support.service_path import ensure_service_in_path

ensure_service_in_path("gateway")

from infrastructure.kosta_legal_ai import build_instructions, extract_output_text


def test_extract_output_text_prefers_output_text_field():
    assert extract_output_text({"output_text": "  hello  "}) == "hello"


def test_extract_output_text_from_message_parts():
    payload = {
        "output": [
            {"type": "reasoning", "content": []},
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Часть 1. "},
                    {"type": "text", "text": "Часть 2."},
                ],
            },
        ]
    }
    assert extract_output_text(payload) == "Часть 1. Часть 2."


def test_build_instructions_legal_uzbekistan_and_command():
    text = build_instructions("tax", 7, "contractAnalysis")
    assert "Узбекистан" in text
    assert "налоговое право" in text
    assert "анализ договора" in text.lower() or "Режим: анализ договора" in text
    assert "7" in text
