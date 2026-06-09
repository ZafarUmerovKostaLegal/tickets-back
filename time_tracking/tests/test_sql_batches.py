from __future__ import annotations

from application.sql_batches import DEFAULT_SQL_IN_BATCH_SIZE, iter_sql_in_batches


def test_iter_sql_in_batches_dedupes_and_chunks() -> None:
    ids = [f"id-{i}" for i in range(25_000)]
    batches = list(iter_sql_in_batches(ids, batch_size=10_000))
    assert len(batches) == 3
    assert len(batches[0]) == 10_000
    assert len(batches[1]) == 10_000
    assert len(batches[2]) == 5_000
    assert len(list(dict.fromkeys(ids))) == 25_000


def test_iter_sql_in_batches_dedupes() -> None:
    ids = ["a", "b", "a", "c", "b"]
    assert list(iter_sql_in_batches(ids)) == [["a", "b", "c"]]


def test_iter_sql_in_batches_under_asyncpg_limit() -> None:
    huge = [str(i) for i in range(40_000)]
    batches = list(iter_sql_in_batches(huge))
    assert all(len(batch) <= DEFAULT_SQL_IN_BATCH_SIZE for batch in batches)
    assert sum(len(batch) for batch in batches) == 40_000
