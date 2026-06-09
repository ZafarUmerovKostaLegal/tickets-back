"""Разбиение больших списков для SQL IN (...).

asyncpg/PostgreSQL: число bind-параметров в одном запросе не может превышать 32767.
"""

from __future__ import annotations

from typing import Iterator, TypeVar

T = TypeVar("T")

# Запас под другие параметры запроса (даты, статусы и т.д.).
DEFAULT_SQL_IN_BATCH_SIZE = 10_000


def iter_sql_in_batches(
    items: list[T],
    *,
    batch_size: int = DEFAULT_SQL_IN_BATCH_SIZE,
) -> Iterator[list[str]]:
    """Уникальные непустые id строками, порциями не больше batch_size."""
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    uniq = list(dict.fromkeys(str(x) for x in items if x is not None and str(x).strip()))
    for start in range(0, len(uniq), batch_size):
        yield uniq[start : start + batch_size]
