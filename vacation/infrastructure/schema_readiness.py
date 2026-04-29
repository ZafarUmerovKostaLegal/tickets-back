

import asyncio

schema_ready_event = asyncio.Event()


def mark_schema_ready() -> None:
    schema_ready_event.set()
