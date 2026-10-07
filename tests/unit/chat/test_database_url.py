from __future__ import annotations

import pytest

from infrastructure.config import Settings, resolve_database_url


@pytest.mark.unit
def test_default_url_is_replaced_when_chat_db_password_is_set():
    settings = Settings(
        database_url="postgresql://chat:chat@chat_db:5432/kosta_chat",
        chat_database_url="",
        chat_db_user="chat",
        chat_db_password="s3cret",
        chat_db_host="chat_db",
        chat_db_port=5432,
        chat_db_name="kosta_chat",
    )
    assert resolve_database_url(settings) == "postgresql://chat:s3cret@chat_db:5432/kosta_chat"


@pytest.mark.unit
def test_explicit_custom_url_is_kept():
    settings = Settings(
        database_url="postgresql://chat:chat@chat_db:5432/kosta_chat",
        chat_database_url="postgresql://chat:realpass@chat_db:5432/kosta_chat",
        chat_db_password="s3cret",
    )
    assert resolve_database_url(settings) == "postgresql://chat:realpass@chat_db:5432/kosta_chat"


@pytest.mark.unit
def test_matching_default_password_stays_on_compose_url():
    settings = Settings(
        database_url="postgresql://chat:chat@chat_db:5432/kosta_chat",
        chat_db_password="chat",
    )
    assert resolve_database_url(settings) == "postgresql://chat:chat@chat_db:5432/kosta_chat"


@pytest.mark.unit
def test_password_with_special_characters_is_encoded():
    settings = Settings(
        database_url="",
        chat_database_url="",
        chat_db_user="chat",
        chat_db_password="p@ss:word",
        chat_db_host="chat_db",
        chat_db_port=5432,
        chat_db_name="kosta_chat",
    )
    assert resolve_database_url(settings) == "postgresql://chat:p%40ss%3Aword@chat_db:5432/kosta_chat"
