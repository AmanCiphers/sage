"""The `memory` tool: agent-curated long-term memory.

SAGE stores durable facts in a bounded markdown file (MemoryStore).
The agent edits it explicitly — there is no automatic extraction yet.
"""

import json

from app.memory.store import MemoryStore
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput


class MemoryInput(ToolInput):
    action: str  # "add" | "remove" | "list" | "clear"
    text: str | None = None


def register_memory_tools(registry, memory_store: MemoryStore) -> None:
    def memory(action: str, text: str | None = None) -> str:
        if action == "add":
            if not text:
                return json.dumps({"error": "text is required for add"})
            result = memory_store.add(text)
        elif action == "remove":
            if not text:
                return json.dumps({"error": "text is required for remove"})
            result = memory_store.remove(text)
        elif action == "list":
            result = memory_store.load() or "(memory is empty)"
        elif action == "clear":
            result = memory_store.clear()
        else:
            return json.dumps({"error": f"unknown action: {action}"})

        if result.startswith("Error"):
            return json.dumps({"error": result})

        return json.dumps({"result": result})

    registry.register(
        ToolDefinition(
            name="memory",
            description=(
                "Manage long-term memory of durable facts about the user "
                "(preferences, projects, goals, decisions). "
                "Actions: add (store a fact), remove (delete matching facts), "
                "list (show all facts), clear (wipe memory)."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=MemoryInput,
            destructive=False,
            employee_eligible=False,
            handler=memory,
        )
    )
