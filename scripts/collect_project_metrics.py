from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = (
    "gateway",
    "auth",
    "tickets",
    "time_tracking",
    "attendance",
    "inventory",
    "notifications",
    "todos",
    "expenses",
    "correspondence",
    "vacation",
    "call_schedule",
    "chat",
    "contacts",
    "backend_common",
)

ENV_DEFAULTS = {
    "JWT_SECRET": "metrics-jwt-secret-min-32-characters-long",
    "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/test",
    "GATEWAY_DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/test",
}


def count_py_lines(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    total = 0
    for path in directory.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            total += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            pass
    return total


def count_tests(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    total = 0
    for path in directory.rglob("test_*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        total += len(re.findall(r"^\s*(?:async\s+)?def test_", text, re.M))
    return total


def pytest_seconds(target: str) -> float | None:
    env = {**os.environ, **ENV_DEFAULTS, "PYTHONPATH": str(ROOT / "tests")}
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--tb=no"],
        cwd=ROOT,
        env=env,
        capture_output=True,
    )
    if proc.returncode not in (0, 1):
        return None
    return round(time.perf_counter() - started, 1)


def gateway_route_stats() -> dict[str, int]:
    sys.path.insert(0, str(ROOT / "tests"))
    from support.service_path import ensure_service_in_path
    from e2e.support.route_discovery import collect_routes

    ensure_service_in_path("gateway")
    from presentation.api import app

    routes = collect_routes(app, service="gateway")
    return {
        "gateway_routes_total": len(routes),
        "gateway_routes_auth": sum(1 for route in routes if route.requires_auth),
    }


def main() -> None:
    for key, value in ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)

    services = []
    for name in SERVICES:
        services.append(
            {
                "name": name,
                "loc": count_py_lines(ROOT / name),
                "unit_tests": count_tests(ROOT / "tests" / "unit" / name),
            }
        )

    unit_root = ROOT / "tests" / "unit"
    by_service = []
    for child in sorted(unit_root.iterdir()):
        if child.is_dir():
            by_service.append({"service": child.name, "tests": count_tests(child)})

    metrics = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "services": services,
        "tests": {
            "unit_tests": sum(s["unit_tests"] for s in services),
            "unit_seconds": pytest_seconds("tests/unit"),
            "e2e_workflow_seconds": pytest_seconds("tests/e2e/workflows"),
            "by_service": by_service,
        },
        "e2e": gateway_route_stats(),
    }

    out = ROOT / "scripts" / "project_metrics.json"
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
