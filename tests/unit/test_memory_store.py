import json

from app.memory.store import MemoryStore
from app.tools.memory_tools import register_memory_tools
from app.tools.registry import ToolRegistry


def build_store(tmp_path, max_chars=200):
    return MemoryStore(tmp_path / "memory.md", max_chars=max_chars)


def test_add_and_load(tmp_path):
    store = build_store(tmp_path)

    result = store.add("User prefers Fedora")
    assert result == "Remembered: User prefers Fedora"
    assert store.load() == "- User prefers Fedora"
    assert store.entries() == ["User prefers Fedora"]


def test_duplicate_add_ignored(tmp_path):
    store = build_store(tmp_path)

    store.add("User prefers Fedora")
    assert store.add("user prefers fedora") == "Already in memory."
    assert len(store.entries()) == 1


def test_remove_by_substring(tmp_path):
    store = build_store(tmp_path)

    store.add("User prefers Fedora")
    store.add("User is learning Rust")

    result = store.remove("fedora")
    assert "Removed 1 entry" in result
    assert store.entries() == ["User is learning Rust"]


def test_remove_no_match(tmp_path):
    store = build_store(tmp_path)
    store.add("User prefers Fedora")

    assert "No memory entry matches" in store.remove("pizza")


def test_capacity_bound(tmp_path):
    store = build_store(tmp_path, max_chars=60)

    store.add("fact one is here")
    result = store.add("a second fact that is much longer than the first")

    assert result.startswith("Error: memory is full")
    assert len(store.entries()) == 1


def test_clear(tmp_path):
    store = build_store(tmp_path)
    store.add("fact")

    assert store.clear() == "Memory cleared."
    assert store.load() == ""


def test_memory_tool_json_contract(tmp_path):
    store = build_store(tmp_path)
    registry = ToolRegistry()
    register_memory_tools(registry, store)

    tool = registry.get("memory")

    added = json.loads(tool.handler(action="add", text="User likes tea"))
    assert "Remembered" in added["result"]

    listed = json.loads(tool.handler(action="list"))
    assert "tea" in listed["result"]

    removed = json.loads(tool.handler(action="remove", text="tea"))
    assert "Removed" in removed["result"]

    bad = json.loads(tool.handler(action="add"))
    assert "error" in bad
