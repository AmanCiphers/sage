"""The `memory` tool: agent-curated long-term memory.

SAGE stores durable facts in a bounded markdown file (MemoryStore).
The agent edits it explicitly — there is no automatic extraction yet.

SAGE writes to its own memory.md; when invoked by an employee, the handler
resolves the current employee's memory.md (per-employee isolation).
"""

import json

from app.memory.store import MemoryStore
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput


class MemoryInput(ToolInput):
    action: str  # "add" | "remove" | "list" | "clear"
    text: str | None = None


def _resolve_store(sage_store: MemoryStore) -> MemoryStore:
    """Return SAGE's store, or the current employee's if an employee is active."""
    from app.config import settings
    from app.tools.workspace import current_employee_key

    key = current_employee_key()
    if key:
        from pathlib import Path

        root = Path(settings.database_path).parent / "employees" / key
        return MemoryStore(root / "memory.md")
    return sage_store


def register_memory_tools(registry, memory_store: MemoryStore) -> None:
    def memory(action: str, text: str | None = None) -> str:
        store = _resolve_store(memory_store)
        if action == "add":
            if not text:
                return json.dumps({"error": "text is required for add"})
            result = store.add(text)
        elif action == "remove":
            if not text:
                return json.dumps({"error": "text is required for remove"})
            result = store.remove(text)
        elif action == "list":
            result = store.load() or "(memory is empty)"
        elif action == "clear":
            result = store.clear()
        else:
            return json.dumps({"error": f"unknown action: {action}"})

        if result.startswith("Error"):
            return json.dumps({"error": result})

        return json.dumps({"result": result})

    registry.register(
        ToolDefinition(
            name="memory",
            description=(
                "Manage long-term memory of durable facts (preferences, "
                "projects, goals, decisions). "
                "Actions: add (store a fact), remove (delete matching facts), "
                "list (show all facts), clear (wipe memory)."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=MemoryInput,
            destructive=False,
            employee_eligible=True,
            handler=memory,
        )
    )
