"""The `memory` tool: agent-curated long-term memory.

SAGE stores durable facts in a bounded markdown file (MemoryStore).
The agent edits it explicitly — there is no automatic extraction yet.

SAGE writes to its own memory.md; when invoked by an employee, the handler
resolves the current employee's memory.md (per-employee isolation).

v2 adds provenance, evolution, forgetting (supersede, forget) with a
JSON-lines backing store (memory_v2.json). Enabled via SAGE_FEATURE_MEMORY_V2.
"""
import json

from app.config import settings
from app.memory.store import MemoryStore
from app.memory.store_v2 import MemoryStoreV2
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput


class MemoryInput(ToolInput):
    action: str  # "add" | "remove" | "list" | "clear" | "status" | "forget" | "supersede"
    text: str | None = None
    old_text: str | None = None  # for supersede


def _resolve_store(sage_store: MemoryStore) -> MemoryStore:
    """Return SAGE's store, or the current employee's if an employee is active."""
    from app.config import settings
    from app.tools.workspace import current_employee_key

    key = current_employee_key()
    if key:
        from pathlib import Path

        root = Path(settings.database_path).parent / "employees" / key
        if settings.feature_memory_v2:
            return MemoryStoreV2(root / "memory_v2.json")
        return MemoryStore(root / "memory.md")
    if settings.feature_memory_v2:
        from pathlib import Path
        return MemoryStoreV2(Path(settings.database_path).parent / "memory_v2.json")
    return sage_store


def register_memory_tools(registry, memory_store: MemoryStore) -> None:
    def memory(action: str, text: str | None = None, old_text: str | None = None) -> str:
        store = _resolve_store(memory_store)
        is_v2 = isinstance(store, MemoryStoreV2)

        if action == "add":
            if not text:
                return json.dumps({"error": "text is required for add"})
            if isinstance(store, MemoryStoreV2):
                result = store.add(text)
                return json.dumps({"result": result, "status": store.status()})
            result = store.add(text)
            return json.dumps({"result": result, "status": store.status()})
        elif action == "remove":
            if not text:
                return json.dumps({"error": "text is required for remove"})
            result = store.remove(text)
            return json.dumps({"result": result, "status": store.status()})
        elif action == "forget":
            if not text:
                return json.dumps({"error": "text is required for forget"})
            if is_v2:
                result = store.forget(text)
            else:
                result = store.remove(text)
            return json.dumps({"result": result, "status": store.status()})
        elif action == "supersede":
            if not text or not old_text:
                return json.dumps({"error": "supersede requires text (new) and old_text"})
            if not isinstance(store, MemoryStoreV2):
                return json.dumps({"error": "supersede requires v2 memory (SAGE_FEATURE_MEMORY_V2=1)"})
            result = store.supersede(old_text, text)
            return json.dumps({"result": result, "status": store.status()})
        elif action == "list":
            result = store.load() or "(memory is empty)"
            return json.dumps({"result": result, "status": store.status()})
        elif action == "clear":
            result = store.clear()
            return json.dumps({"result": result, "status": store.status()})
        elif action == "status":
            return json.dumps({"result": "Memory status retrieved", "status": store.status()})
        else:
            return json.dumps({"error": f"unknown action: {action}"})

    registry.register(
        ToolDefinition(
            name="memory",
            description=(
                "Manage long-term memory of durable facts (preferences, "
                "projects, goals, decisions). "
                "Actions: add (store a fact), remove (delete matching facts), "
                "forget (alias remove), supersede (replace old fact with new), "
                "list (show all facts), clear (wipe memory), status (capacity)."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=MemoryInput,
            destructive=False,
            employee_eligible=True,
            handler=memory,
        )
    )