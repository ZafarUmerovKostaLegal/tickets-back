

def sync_engine_url(database_url: str) -> str:
    u = database_url.strip()
    if u.startswith("postgresql+asyncpg://"):
        return u.replace("postgresql+asyncpg://", "postgresql://", 1)
    return u
