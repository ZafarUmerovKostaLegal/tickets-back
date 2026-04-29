

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SERVICE_DIR_NAMES = (
    "gateway",
    "auth",
    "tickets",
    "notifications",
    "inventory",
    "attendance",
    "time_tracking",
    "todos",
    "expenses",
    "call_schedule",
    "projects",
    "vacation",
    "telegram_bot",
)


def ensure_service_in_path(service: str) -> None:

    service_paths = {str((_ROOT / s).resolve()) for s in _SERVICE_DIR_NAMES if (_ROOT / s).is_dir()}
    for p in list(sys.path):
        try:
            if Path(p).resolve() in {Path(x).resolve() for x in service_paths}:
                sys.path.remove(p)
        except (OSError, ValueError):
            pass
    target = _ROOT / service
    if target.is_dir():
        sys.path.insert(0, str(target.resolve()))
    to_remove = [
        k
        for k in sys.modules
        if k in ("presentation", "infrastructure", "application", "domain")
        or k.startswith(("presentation.", "infrastructure.", "application.", "domain."))
    ]
    for k in to_remove:
        del sys.modules[k]


_ensure_service_in_path = ensure_service_in_path
