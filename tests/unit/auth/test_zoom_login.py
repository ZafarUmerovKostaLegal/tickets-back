import asyncio

import httpx
import pytest

import infrastructure.config as config
from infrastructure.zoom_oauth import ZoomAuthError, zoom_identity


@pytest.fixture()
def zoom_env(monkeypatch):
    monkeypatch.setenv("ZOOM_ACCOUNT_ID", "firm-acc")
    monkeypatch.setenv("ZOOM_APP_CLIENT_ID", "app")
    monkeypatch.setenv("ZOOM_APP_CLIENT_SECRET", "secret")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _zoom(account_id: str, seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "user-token"})
        if request.url.path == "/v2/users/me":
            return httpx.Response(200, json={"id": "z1", "email": "Iroda@KostaLegal.com", "account_id": account_id})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_firm_zoom_account_gives_the_email(zoom_env):
    seen: list[httpx.Request] = []
    identity = asyncio.run(zoom_identity("code", "v" * 43, transport=_zoom("firm-acc", seen)))
    assert identity.email == "iroda@kostalegal.com"
    token_request = seen[0]
    assert b"code_verifier=" + b"v" * 43 in token_request.content
    assert b"redirect_uri=https%3A%2F%2Ftickets.kostalegal.com%2Foutlook-addin%2Fzoom.html" in token_request.content


def test_foreign_zoom_account_is_refused(zoom_env):
    with pytest.raises(ZoomAuthError) as error:
        asyncio.run(zoom_identity("code", "v" * 43, transport=_zoom("someone-else", [])))
    assert error.value.status == 403


def test_routes_module_loads():
    from presentation.routes import outlook_auth_routes

    assert any(getattr(route, "path", "") == "/auth/outlook/zoom/bootstrap" for route in outlook_auth_routes.router.routes)
