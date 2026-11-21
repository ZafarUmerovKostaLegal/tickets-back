from types import SimpleNamespace

from presentation.routes.ws_tickets import _comment_owner_denied


class _Repo:
    def __init__(self, comments):
        self._comments = comments

    async def get_by_id(self, comment_id):
        return self._comments.get(comment_id)


_REPO = _Repo({7: SimpleNamespace(id=7, user_id=42, ticket_id=1)})


async def test_author_may_change_own_comment():
    assert await _comment_owner_denied(_REPO, {"comment_id": 7, "acting_user_id": 42}) is None


async def test_other_user_is_denied():
    err = await _comment_owner_denied(_REPO, {"comment_id": 7, "acting_user_id": 5})
    assert err


async def test_moderator_allowed():
    payload = {"comment_id": 7, "acting_user_id": 5, "acting_can_moderate": True}
    assert await _comment_owner_denied(_REPO, payload) is None


async def test_missing_identity_is_denied():
    assert await _comment_owner_denied(_REPO, {"comment_id": 7})
    assert await _comment_owner_denied(_REPO, {"comment_id": 7, "acting_user_id": 5, "acting_can_moderate": "true"})
