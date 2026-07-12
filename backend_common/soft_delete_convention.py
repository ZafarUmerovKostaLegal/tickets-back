"""Soft-delete / archive field conventions across domains (documentation helper).

Do not migrate columns to a single name — domains already use different semantics.
This module is for docs/tests only; importing it must not change data.
"""

SOFT_DELETE_CONVENTION = {
    "voided_at": (
        "time_tracking_entries — accounting soft-remove (unbill); row kept for history"
    ),
    "is_archived": (
        "auth/TT users, clients, projects, tickets, inventory, notifications, todos cards — "
        "hide from active lists without wipe"
    ),
    "deleted_at": "chat_messages — soft-delete message body; attachments may remain",
    "archived_at": (
        "correspondence docs / todos boards / TT entry_archives.archived_at — "
        "archive event timestamp (archives table is not soft-delete of live entry)"
    ),
}
