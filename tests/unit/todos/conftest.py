import importlib
import os

from support.service_path import ensure_service_in_path

ensure_service_in_path("todos")

_previous = os.environ.get("DATABASE_URL")
os.environ["DATABASE_URL"] = os.environ.get(
    "TODOS_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test"
)
try:
    importlib.import_module("infrastructure.config").get_settings.cache_clear()
    importlib.import_module("infrastructure.database")
finally:
    if _previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = _previous
