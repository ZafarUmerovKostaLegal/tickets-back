from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount


@dataclass(frozen=True)
class RouteSpec:
    service: str
    path: str
    method: str
    name: str
    requires_auth: bool


_AUTH_DEPENDENCY_MARKERS = (
    "verify_bearer",
    "get_current_user",
    "require_bearer_user",
    "require_tt_reports_viewer",
    "require_reports_view_role",
    "require_view_role",
    "require_manage",
    "invoice_actor_auth_user_id",
    "verify_access_token",
)


_PUBLIC_PATH_PREFIXES = (
    "/live",
    "/ready",
    "/metrics",
    "/health",
    "/auth/callback",
    "/desktop_backgrounds/",
    "/api/v1/auth/azure/login",
    "/api/v1/auth/azure/callback",
    "/api/v1/auth/azure/logout",
    "/api/v1/auth/admin/login",
    "/api/v1/auth/admin/bootstrap",
    "/api/v1/tickets/ws-url",
    "/api/v1/tickets/statuses",
    "/api/v1/tickets/priorities",
    "/api/v1/expenses/",
    "/api/v1/vacations/leave-requests/email-action",
)


def _is_public_path(path: str) -> bool:
    if path in ("/live", "/ready", "/metrics", "/health"):
        return True
    return any(path.startswith(p) for p in _PUBLIC_PATH_PREFIXES)


def _dependant_requires_auth(dependant) -> bool:
    stack = [dependant]
    seen: set[int] = set()
    while stack:
        dep = stack.pop()
        dep_id = id(dep)
        if dep_id in seen:
            continue
        seen.add(dep_id)
        call = getattr(dep, "call", None)
        if call is not None:
            qual = getattr(call, "__qualname__", "") or getattr(call, "__name__", "")
            mod = getattr(getattr(call, "__module__", ""), "strip", lambda: "")()
            blob = f"{mod}.{qual}".lower()
            if any(m in blob for m in _AUTH_DEPENDENCY_MARKERS):
                return True
        stack.extend(getattr(dep, "dependencies", []) or [])
        stack.extend(getattr(dep, "security_requirements", []) or [])
    return False


def _route_requires_auth(route: APIRoute) -> bool:
    if _is_public_path(route.path):
        return False
    if route.path.endswith("/email-action") or route.path.endswith("/email-file"):
        return False
    return _dependant_requires_auth(route.dependant)


def collect_routes(app, *, service: str) -> list[RouteSpec]:
    out: list[RouteSpec] = []

    def walk(routes: Iterable, prefix: str = "") -> None:
        for route in routes:
            if isinstance(route, Mount):
                mount_path = prefix + (route.path or "")
                walk(route.routes, prefix=mount_path)
                continue
            if isinstance(route, APIRoute):
                methods = sorted(m for m in route.methods if m not in {"HEAD", "OPTIONS"})
                requires_auth = _route_requires_auth(route)
                for method in methods:
                    out.append(
                        RouteSpec(
                            service=service,
                            path=route.path,
                            method=method,
                            name=route.name or "",
                            requires_auth=requires_auth,
                        )
                    )
            elif isinstance(route, APIWebSocketRoute):
                out.append(
                    RouteSpec(
                        service=service,
                        path=route.path,
                        method="WEBSOCKET",
                        name=route.name or "",
                        requires_auth=_dependant_requires_auth(route.dependant),
                    )
                )

    walk(app.routes)
    return out


def fill_path_params(path: str) -> str:
    replacements = {
        "{ticket_uuid}": "00000000-0000-4000-8000-000000000001",
        "{uuid}": "00000000-0000-4000-8000-000000000001",
        "{request_id}": "00000000-0000-4000-8000-000000000002",
        "{group_by}": "clients",
        "{user_id}": "1",
        "{auth_user_id}": "1",
        "{manager_auth_user_id}": "1",
        "{client_id}": "client-1",
        "{project_id}": "project-1",
        "{task_id}": "task-1",
        "{contact_id}": "contact-1",
        "{category_id}": "cat-1",
        "{team_id}": "team-1",
        "{entry_id}": "entry-1",
        "{rate_id}": "rate-1",
        "{snapshot_id}": "00000000-0000-4000-8000-000000000003",
        "{row_id}": "row-1",
        "{invoice_id}": "00000000-0000-4000-8000-000000000004",
        "{id}": "1",
        "{role_id}": "1",
        "{comment_id}": "1",
        "{item_id}": "1",
        "{filename}": "test.bin",
        "{subpath:path}": "test/file.bin",
        "{path:path}": "health",
        "{employee_id}": "1",
        "{absence_day_id}": "1",
        "{attachment_id}": "att-1",
        "{document_id}": "00000000-0000-4000-8000-000000000005",
    }
    out = path
    for key, val in replacements.items():
        out = out.replace(key, val)
    if "{" in out:
        import re
        out = re.sub(r"\{[^}]+\}", "1", out)
    return out
