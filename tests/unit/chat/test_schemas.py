from __future__ import annotations

import pytest
from pydantic import ValidationError

from presentation.schemas import CreatePollBody, PostMessageBody, VotePollBody


@pytest.mark.unit
def test_post_message_body():
    body = PostMessageBody(body="hello")
    assert body.body == "hello"


@pytest.mark.unit
def test_create_poll_requires_two_options():
    with pytest.raises(ValidationError):
        CreatePollBody(question="Q?", options=["only one"])


@pytest.mark.unit
def test_vote_poll_body():
    body = VotePollBody(option_index=0)
    assert body.option_index == 0
