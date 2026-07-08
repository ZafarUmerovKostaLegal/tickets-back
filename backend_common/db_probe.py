from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

_LOAD_HIGH_CONNECTION_RATIO = 0.7
_LOAD_HIGH_ACTIVE_QUERIES = 10

_PG_METRICS_SQL = """
SELECT
  current_database() AS db_name,
  pg_database_size(current_database()) AS size_bytes,
  (
    SELECT count(*)::int
    FROM pg_stat_activity
    WHERE datname = current_database()
  ) AS connections,
  (
    SELECT count(*)::int
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND state = 'active'
      AND pid != pg_backend_pid()
  ) AS active_queries,
  (
    SELECT setting::int
    FROM pg_settings
    WHERE name = 'max_connections'
  ) AS max_connections,
  extract(epoch FROM (now() - pg_postmaster_start_time()))::bigint AS uptime_seconds,
  coalesce(
    (
      SELECT round(100.0 * sum(blks_hit) / nullif(sum(blks_hit) + sum(blks_read), 0), 2)
      FROM pg_stat_database
      WHERE datname = current_database()
    ),
    0
  ) AS cache_hit_pct
"""


def redact_database_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("redis://") or raw.startswith("rediss://"):
        parsed = urlparse(raw)
        host = parsed.hostname or "?"
        port = parsed.port or 6379
        db = (parsed.path or "/0").lstrip("/") or "0"
        return f"redis://***@{host}:{port}/{db}"
    parsed = urlparse(raw)
    host = parsed.hostname or "?"
    port = parsed.port or 5432
    db = (parsed.path or "/").lstrip("/") or "?"
    return f"postgresql://***@{host}:{port}/{db}"


def classify_postgres_load(*, connections: int, max_connections: int, active_queries: int) -> str:
    if max_connections > 0 and connections / max_connections >= _LOAD_HIGH_CONNECTION_RATIO:
        return "high"
    if active_queries >= _LOAD_HIGH_ACTIVE_QUERIES:
        return "high"
    if max_connections > 0 and connections / max_connections >= 0.4:
        return "moderate"
    return "low"


def format_bytes(num: int | float | None) -> str:
    value = float(num or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def _to_asyncpg_dsn(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql+asyncpg://"):
        return u.replace("postgresql+asyncpg://", "postgresql://", 1)
    return u


async def probe_postgresql(name: str, url: str, *, timeout_sec: float = 3.0) -> dict[str, Any]:
    started = time.monotonic()
    dsn = _to_asyncpg_dsn(url)
    if not dsn:
        return {
            "name": name,
            "kind": "postgresql",
            "status": "not_configured",
            "host": None,
            "database": None,
            "latencyMs": None,
            "load": "unknown",
            "metrics": {},
            "error": "URL not configured",
        }
    parsed = urlparse(dsn)
    host = parsed.hostname
    database = (parsed.path or "/").lstrip("/") or None
    try:
        import asyncpg

        conn = await asyncpg.connect(dsn, timeout=timeout_sec)
        try:
            row = await conn.fetchrow(_PG_METRICS_SQL)
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            connections = int(row["connections"] or 0)
            max_connections = int(row["max_connections"] or 0)
            active_queries = int(row["active_queries"] or 0)
            load = classify_postgres_load(
                connections=connections,
                max_connections=max_connections,
                active_queries=active_queries,
            )
            size_bytes = int(row["size_bytes"] or 0)
            return {
                "name": name,
                "kind": "postgresql",
                "status": "ok",
                "host": host,
                "database": row["db_name"] or database,
                "latencyMs": latency_ms,
                "load": load,
                "metrics": {
                    "sizeBytes": size_bytes,
                    "sizeHuman": format_bytes(size_bytes),
                    "connections": connections,
                    "maxConnections": max_connections,
                    "connectionPct": round(100.0 * connections / max_connections, 1)
                    if max_connections
                    else None,
                    "activeQueries": active_queries,
                    "uptimeSeconds": int(row["uptime_seconds"] or 0),
                    "cacheHitPct": float(row["cache_hit_pct"] or 0),
                },
            }
        finally:
            await conn.close()
    except Exception as exc:
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        return {
            "name": name,
            "kind": "postgresql",
            "status": "error",
            "host": host,
            "database": database,
            "latencyMs": latency_ms,
            "load": "unknown",
            "metrics": {},
            "error": str(exc)[:500],
        }


def _parse_redis_db_index(url: str) -> int:
    path = urlparse(url).path or "/0"
    match = re.match(r"^/(\d+)$", path)
    return int(match.group(1)) if match else 0


async def probe_redis(url: str, *, timeout_sec: float = 3.0) -> dict[str, Any]:
    started = time.monotonic()
    raw = (url or "").strip()
    if not raw:
        return {
            "name": "redis",
            "kind": "redis",
            "status": "not_configured",
            "host": None,
            "database": None,
            "latencyMs": None,
            "load": "unknown",
            "metrics": {},
            "error": "REDIS_URL not configured",
        }
    parsed = urlparse(raw)
    host = parsed.hostname
    port = parsed.port or 6379
    db_index = _parse_redis_db_index(raw)
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(raw, socket_connect_timeout=timeout_sec, socket_timeout=timeout_sec)
        try:
            pong = await client.ping()
            if not pong:
                raise RuntimeError("PING returned false")
            info = await client.info(section="memory")
            clients_info = await client.info(section="clients")
            stats_info = await client.info(section="stats")
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            used_memory = int(info.get("used_memory", 0) or 0)
            connected_clients = int(clients_info.get("connected_clients", 0) or 0)
            ops_per_sec = int(stats_info.get("instantaneous_ops_per_sec", 0) or 0)
            load = "high" if connected_clients >= 50 or ops_per_sec >= 500 else (
                "moderate" if connected_clients >= 20 or ops_per_sec >= 100 else "low"
            )
            return {
                "name": "redis",
                "kind": "redis",
                "status": "ok",
                "host": host,
                "database": str(db_index),
                "latencyMs": latency_ms,
                "load": load,
                "metrics": {
                    "usedMemoryBytes": used_memory,
                    "usedMemoryHuman": format_bytes(used_memory),
                    "connectedClients": connected_clients,
                    "opsPerSec": ops_per_sec,
                    "port": port,
                },
            }
        finally:
            await client.aclose()
    except Exception as exc:
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        return {
            "name": "redis",
            "kind": "redis",
            "status": "error",
            "host": host,
            "database": str(db_index),
            "latencyMs": latency_ms,
            "load": "unknown",
            "metrics": {},
            "error": str(exc)[:500],
        }
