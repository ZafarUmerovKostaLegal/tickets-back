from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class User:
    id: int
    azure_oid: str
    email: str
    display_name: Optional[str]
    picture: Optional[str]
    role: str
    position: Optional[str]
    is_blocked: bool
    is_archived: bool
    time_tracking_role: Optional[str]
    desktop_background: Optional[str]

    active_session_jti: Optional[str]
    created_at: datetime
    updated_at: datetime
