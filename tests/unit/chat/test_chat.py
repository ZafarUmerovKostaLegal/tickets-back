from __future__ import annotations

import pytest


@pytest.mark.unit
def test_chat_api_prefix_constant():
    from presentation.api import CHAT_API_PREFIX

    assert CHAT_API_PREFIX == "/api/v1/chat"


@pytest.mark.unit
def test_post_message_body_schema():
    from presentation.schemas import PostMessageBody

    body = PostMessageBody(body="hi")
    assert body.body == "hi"
