from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from presentation.routes import outlook_auth_routes

    outlook_auth_routes._relay.clear()
    app = FastAPI()
    app.include_router(outlook_auth_routes.router)
    return TestClient(app)


def test_relay_hands_the_code_over_once():
    client = _client()
    state = "tr." + "a" * 43
    assert client.get("/auth/outlook/relay", params={"state": state}).status_code == 204
    assert client.post("/auth/outlook/relay", json={"state": state, "code": "abc"}).status_code == 204
    taken = client.get("/auth/outlook/relay", params={"state": state})
    assert taken.status_code == 200 and taken.json()["code"] == "abc"
    assert client.get("/auth/outlook/relay", params={"state": state}).status_code == 204


def test_relay_rejects_short_or_foreign_states():
    client = _client()
    assert client.post("/auth/outlook/relay", json={"state": "short", "code": "x"}).status_code == 422
    assert client.post("/auth/outlook/relay", json={"state": "xx." + "a" * 43, "code": "x"}).status_code == 422
