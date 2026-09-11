import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from support.service_path import ensure_service_in_path

ensure_service_in_path("contacts")
