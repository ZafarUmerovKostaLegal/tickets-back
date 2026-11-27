import asyncio
import os
from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import httpx
import pytest

from support.service_path import ensure_service_in_path

PARTNER = {"id": 10, "role": "Партнёр", "display_name": "Partner A"}
LAWYER = {"id": 11, "role": "Сотрудник", "display_name": "Lawyer A"}
JUNIOR = {"id": 12, "role": "Сотрудник", "display_name": "Junior A"}
OUTSIDER = {"id": 20, "role": "Сотрудник", "display_name": "Lawyer B"}
GUEST = {"id": 30, "role": "Сотрудник", "display_name": "Guest"}

TEAM_A = {"id": "team-a", "name": "Team A", "partner_auth_user_id": 10, "member_auth_user_ids": [11, 12],
          "members": [{"auth_user_id": 11, "display_name": "Lawyer A"}, {"auth_user_id": 12, "display_name": "Junior A"}]}
TEAM_B = {"id": "team-b", "name": "Team B", "partner_auth_user_id": 21, "member_auth_user_ids": [20], "members": []}
MEMBERSHIP = {10: [TEAM_A], 11: [TEAM_A], 12: [TEAM_A], 20: [TEAM_B], 30: []}


@pytest.fixture()
def hub(monkeypatch):
    ensure_service_in_path("legal_hub")
    from fastapi import FastAPI

    import application.task_mail as task_mail
    import application.teams as teams
    import presentation.routes.tasks as tasks_routes
    from infrastructure import models  # noqa: F401
    from infrastructure.database import Base, engine
    from presentation.deps import require_user
    from presentation.routes.tasks import router as tasks_router
    from presentation.routes.teams import router as teams_router

    async def fake_teams(user):
        return MEMBERSHIP.get(int(user["id"]), [])

    async def no_mail(*args, **kwargs):
        return None

    monkeypatch.setattr(teams, "_fetch_my_teams", fake_teams)
    monkeypatch.setattr(task_mail, "notify_assignee", no_mail)
    monkeypatch.setattr(tasks_routes, "notify_assignee", no_mail)

    current = {"user": PARTNER}
    loop = asyncio.new_event_loop()
    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/v1/legal-hub")
    app.include_router(teams_router, prefix="/api/v1/legal-hub")
    app.dependency_overrides[require_user] = lambda: current["user"]

    async def reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    loop.run_until_complete(reset())

    class Client:
        def as_user(self, user):
            current["user"] = user
            return self

        def call(self, method, path, **kwargs):
            async def run():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                    return await client.request(method, f"/api/v1/legal-hub{path}", **kwargs)

            return loop.run_until_complete(run())

        def ok(self, method, path, **kwargs):
            response = self.call(method, path, **kwargs)
            assert response.status_code < 300, response.text
            return response.json()

        def task(self, title, **extra):
            return self.ok("POST", "/tasks", json={"title": title, **extra})

    yield Client()

    async def dispose():
        await engine.dispose()

    loop.run_until_complete(dispose())
    loop.close()


def test_tasks_are_visible_inside_the_team_and_assignable_to_anyone(hub):
    task = hub.as_user(PARTNER).task("SPA review", assigneeUserId=12, reviewerUserId=11, projectLabel="Falcon")

    # Any role inside the team can see it and work on it.
    assert hub.as_user(LAWYER).ok("GET", f"/tasks/{task['id']}")["title"] == "SPA review"
    assert [t["id"] for t in hub.ok("GET", "/tasks?view=team")["items"]] == [task["id"]]

    # Another team does not even learn the task exists.
    hub.as_user(OUTSIDER)
    assert hub.call("GET", f"/tasks/{task['id']}").status_code == 404
    assert hub.call("POST", f"/tasks/{task['id']}/comments", json={"text": "hi"}).status_code == 404
    assert hub.ok("GET", "/tasks?view=team")["items"] == []

    # Anyone may give a task to any colleague, even from another team; the assignee then sees it.
    given = hub.as_user(LAWYER).call("POST", "/tasks", json={"title": "x", "assigneeUserId": 20, "reviewerUserId": 20})
    assert given.status_code == 201
    assert hub.as_user(OUTSIDER).ok("GET", f"/tasks/{given.json()['id']}")["title"] == "x"
    assert hub.as_user(LAWYER).call("POST", "/tasks", json={"title": "bad", "assigneeUserId": -3}).status_code == 400
    # A junior may hand work to the partner — role does not matter inside the team.
    assert hub.as_user(JUNIOR).call("POST", "/tasks", json={"title": "y", "assigneeUserId": 10}).status_code == 201


def test_temporary_member_joins_the_team_until_the_date(hub):
    hub.as_user(PARTNER)
    task = hub.task("DD memo", assigneeUserId=11)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    assert hub.as_user(LAWYER).call("POST", "/teams/team-a/temp-access",
                                    json={"userId": 30, "expiresOn": tomorrow}).status_code == 403
    grant = hub.as_user(PARTNER).ok("POST", "/teams/team-a/temp-access",
                                    json={"userId": 30, "userName": "Guest", "expiresOn": tomorrow})
    assert grant["active"] is True
    assert hub.call("POST", "/teams/team-a/temp-access",
                    json={"userId": 31, "expiresOn": "2020-01-01"}).status_code == 400

    # The guest sees the team, its tasks, and can be assigned work.
    teams = hub.as_user(GUEST).ok("GET", "/teams")["teams"]
    assert [(t["id"], t["viaTemp"]) for t in teams] == [("team-a", True)]
    assert hub.ok("GET", f"/tasks/{task['id']}")["title"] == "DD memo"
    card = next(t for t in hub.ok("GET", "/tasks?view=team")["items"] if t["id"] == task["id"])
    assert (card["commentCount"], card["fileCount"], card["checkTotal"], card["checkDone"]) == (0, 0, 0, 0)
    assert hub.as_user(LAWYER).call("PATCH", f"/tasks/{task['id']}", json={"assigneeUserId": 30}).status_code == 200
    assert 30 in hub.ok("GET", "/teams")["mates"]

    # Revoked → back outside.
    hub.as_user(PARTNER).ok("DELETE", f"/teams/team-a/temp-access/{grant['id']}")
    hub.as_user(LAWYER).ok("PATCH", f"/tasks/{task['id']}", json={"assigneeUserId": 11})
    assert hub.as_user(GUEST).call("GET", f"/tasks/{task['id']}").status_code == 404


def test_review_workflow_take_submit_return_approve_reassign(hub):
    task = hub.as_user(PARTNER).task("Board minutes", assigneeUserId=12, status="todo")

    junior = hub.as_user(JUNIOR)
    assert junior.ok("GET", f"/tasks/{task['id']}")["actions"] == ["take", "submit", "reassign"]
    assert junior.ok("POST", f"/tasks/{task['id']}/action", json={"action": "take"})["status"] == "progress"
    assert junior.call("POST", f"/tasks/{task['id']}/action", json={"action": "submit"}).status_code == 400
    sent = junior.ok("POST", f"/tasks/{task['id']}/action", json={"action": "submit", "userId": 11})
    assert (sent["status"], sent["reviewerUserId"]) == ("associate", 11)
    assert junior.call("POST", f"/tasks/{task['id']}/action", json={"action": "approve"}).status_code == 409

    lawyer = hub.as_user(LAWYER)
    assert lawyer.call("POST", f"/tasks/{task['id']}/action", json={"action": "return"}).status_code == 400
    back = lawyer.ok("POST", f"/tasks/{task['id']}/action", json={"action": "return", "comment": "Fix clause 4"})
    assert back["status"] == "changes" and back["comments"][-1]["text"] == "Fix clause 4"

    hub.as_user(JUNIOR).ok("POST", f"/tasks/{task['id']}/action", json={"action": "submit"})
    done = hub.as_user(LAWYER).ok("POST", f"/tasks/{task['id']}/action", json={"action": "approve"})
    assert done["boardColumn"] == "done"
    assert [e["kind"] for e in done["history"]] == ["created", "taken", "submitted", "returned", "submitted", "approved"]

    other = hub.as_user(PARTNER).task("Letter", assigneeUserId=11)
    assert hub.as_user(LAWYER).call("POST", f"/tasks/{other['id']}/action",
                                    json={"action": "reassign", "userId": 20}).status_code == 200
    hub.as_user(PARTNER)
    moved = hub.ok("POST", f"/tasks/{other['id']}/action", json={"action": "reassign", "userId": 12})
    assert (moved["assigneeUserId"], moved["status"]) == (12, "todo")


def test_board_drag_cannot_skip_review(hub):
    task = hub.as_user(PARTNER).task("Lease memo", assigneeUserId=11)
    lawyer = hub.as_user(LAWYER)
    # Free columns move freely (drag and drop on the board).
    assert lawyer.ok("PATCH", f"/tasks/{task['id']}", json={"boardColumn": "waitClient", "status": "wait"})["boardColumn"] == "waitClient"
    # Review and approval only through the workflow actions.
    for patch in ({"boardColumn": "done", "approval": True}, {"boardColumn": "associate"}, {"status": "partner"}, {"approval": True}):
        assert lawyer.call("PATCH", f"/tasks/{task['id']}", json=patch).status_code == 409, patch
    # Saving the same column again (e.g. other fields of a task already in review) is fine.
    lawyer.ok("POST", f"/tasks/{task['id']}/action", json={"action": "submit", "userId": 10})
    assert lawyer.call("PATCH", f"/tasks/{task['id']}", json={"boardColumn": "partner", "title": "Lease memo v2"}).status_code < 300


def test_project_map_and_stats(hub):
    hub.as_user(PARTNER)
    late = (date.today() - timedelta(days=2)).isoformat()
    a = hub.task("A1", assigneeUserId=11, projectId="p1", projectLabel="Falcon", status="progress", dueOn=late)
    hub.task("A2", assigneeUserId=12, projectId="p1", projectLabel="Falcon", status="progress")
    hub.task("A3", assigneeUserId=12, projectId="p1", projectLabel="Falcon", status="associate")
    b = hub.task("B1", assigneeUserId=11, projectId="p2", projectLabel="Orion", status="associate", reviewerUserId=10)
    hub.ok("POST", f"/tasks/{b['id']}/action", json={"action": "approve"})

    projects = {p["id"]: p for p in hub.ok("GET", "/projects/map")["projects"]}
    assert projects["p1"]["stage"] == "progress" and projects["p1"]["overdue"] == 1
    assert projects["p1"]["stages"]["review"] == 1
    assert (projects["p2"]["stage"], projects["p2"]["progress"]) == ("done", 100)

    stats = hub.ok("GET", "/stats")
    assert stats["totals"]["open"] == 3 and stats["totals"]["overdue"] == 1 and stats["totals"]["done30"] == 1
    people = {p["userId"]: p for p in stats["people"]}
    assert people[11]["overdue"] == 1 and people[12]["open"] == 2
    assert a["id"]
    # Another team sees none of it.
    assert hub.as_user(OUTSIDER).ok("GET", "/projects/map")["projects"] == []
