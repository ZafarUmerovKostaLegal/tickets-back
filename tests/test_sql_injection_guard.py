

import pytest

from backend_common.sql_injection_guard import contains_sql_injection_pattern


@pytest.mark.parametrize(
    "safe",
    [
        "hello world",
        "ticket about budget",
        "user@example.com",
        "2024-01-15",
        "",
        "a",
    ],
)
def test_allows_normal_text(safe: str) -> None:
    assert not contains_sql_injection_pattern(safe)


@pytest.mark.parametrize(
    "malicious",
    [
        "1' OR '1'='1",
        "admin'--",
        "1; DROP TABLE users--",
        "UNION SELECT null,null,null",
        "union all select 1,2,3",
        "1 AND 1=1",
        "1 OR 1=1",
        "sleep(5)",
        "pg_sleep(10)",
        "@@version",
        "1=1",
        "/**/OR/**/1=1",
        "information_schema.tables",
    ],
)
def test_blocks_common_payloads(malicious: str) -> None:
    assert contains_sql_injection_pattern(malicious)
