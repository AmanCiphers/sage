import json

import pytest

from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import MessageStore
from app.tools.registry import ToolRegistry
from app.tools.session_search import register_session_search


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


async def test_search_finds_matching_messages(database):
    store = MessageStore(database)

    await store.append_user("my favourite food is pizza")
    await store.append_assistant("Nice choice!")
    await store.append_user("what is my favourite food?")

    results = await store.search("favourite food")

    assert len(results) == 2
    assert results[0]["role"] == "user"
    assert "pizza" in results[0]["content"]


async def test_search_no_match(database):
    store = MessageStore(database)
    await store.append_user("hello there")

    assert await store.search("unicorns") == []


async def test_session_search_tool(database):
    store = MessageStore(database)
    await store.append_user("remember that I love neapolitan pizza")

    registry = ToolRegistry()
    register_session_search(registry, store)

    tool = registry.get("session_search")
    result = json.loads(await tool.handler(query="neapolitan", limit=3))

    assert len(result["results"]) == 1
    assert "neapolitan" in result["results"][0]["content"]


async def test_fts_backfill_existing_rows(tmp_path):
    # Simulate a pre-FTS database: raw messages exist, fts is empty.
    db = Database(tmp_path / "legacy.db")
    await db.connect()
    await initialize_schema(db)

    await db.connection.execute(
        "INSERT INTO messages (role, content) VALUES ('user', 'legacy message about cats')"
    )
    await db.connection.execute("DELETE FROM messages_fts")
    await db.connection.commit()
    await db.close()

    # Re-initializing (app restart) should backfill.
    db2 = Database(tmp_path / "legacy.db")
    await db2.connect()
    await initialize_schema(db2)

    store = MessageStore(db2)
    results = await store.search("cats")

    assert len(results) == 1
    assert results[0]["content"] == "legacy message about cats"

    await db2.close()
