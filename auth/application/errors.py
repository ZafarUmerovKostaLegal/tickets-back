class UserAccessDenied(Exception):
    """Blocked or archived user cannot authenticate or keep a session."""

    def __init__(self, reason: str = "blocked_or_archived"):
        self.reason = reason
        super().__init__(reason)
