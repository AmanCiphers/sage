import pytest

from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import MessageStore, SessionStateStore, TaskStore


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


async def test_message_roundtrip_preserves_order(database):
    store = MessageStore(database)

    await store.append_user("first")
    await store.append_assistant(
        "calling",
        tool_calls=[{"id": "call_1", "function": {"name": "t", "arguments": "{}"}}],
    )
    await store.append_tool_result("call_1", "result")
    await store.append_assistant("final")

    messages = await store.recent(50)

    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1]["tool_calls"][0]["id"] == "call_1"
    assert messages[2]["tool_call_id"] == "call_1"


async def test_recent_limit_returns_newest(database):
    store = MessageStore(database)

    for i in range(10):
        await store.append_user(f"m{i}")

    messages = await store.recent(3)

    assert [m["content"] for m in messages] == ["m7", "m8", "m9"]


async def test_session_state_summary_and_strikes(database):
    store = SessionStateStore(database)

    state = await store.get()
    assert state["summary"] is None
    assert state["ineffective_strikes"] == 0

    await store.save_summary("a summary")
    await store.set_strikes(1)

    state = await store.get()
    assert state["summary"] == "a summary"
    assert state["ineffective_strikes"] == 1
    assert state["compaction_count"] == 1


async def test_task_lifecycle(database):
    store = TaskStore(database)

    task_id = await store.create(
        objective="obj",
        tool_name="some_tool",
        arguments={"a": 1},
    )

    task = await store.get(task_id)
    assert task["status"] == "queued"
    assert task["arguments"] == {"a": 1}

    await store.set_status(task_id, "completed", result="output")

    task = await store.get(task_id)
    assert task["status"] == "completed"
    assert task["result"] == "output"
