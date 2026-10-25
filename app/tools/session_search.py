"""The `session_search` tool: full-text search over raw conversation history,
skills, and memories.

SQLite FTS5 over the messages table. Lets SAGE recall anything from the
permanent raw history even after compaction removed it from the context.
Also searches skill store and memory v2 when enabled.
"""

import json
from typing import List, Optional

from app.persistence.store import MessageStore
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput


class SessionSearchInput(ToolInput):
    query: str
    limit: int = 5
    sources: Optional[List[str]] = None  # e.g., ["conversations", "skills", "memories"]


def register_session_search(registry, message_store: MessageStore) -> None:
    async def session_search(query: str, limit: int = 5, sources: Optional[List[str]] = None) -> str:
        srcs = sources or ["conversations"]
        # Conversations
        conv_results = []
        if "conversations" in srcs:
            conv_results = await message_store.search(query, limit)
        # If only conversations requested and no other sources, return legacy format for backward compatibility
        if srcs == ["conversations"] and not any(s in srcs for s in ("skills", "memories")):
            return json.dumps({"results": conv_results})
        # Otherwise build full result dict
        results = {"conversations": conv_results, "skills": [], "memories": []}
        # Skills
        if "skills" in srcs:
            try:
                from app.skills.store import SkillStore
                from app.config import settings
                from app.tools.workspace import current_employee_key
                from pathlib import Path
                key = current_employee_key()
                if key:
                    root = Path(settings.database_path).parent / "employees" / key
                    skill_store = SkillStore(root / "skills.jsonl")
                else:
                    skill_store = SkillStore(Path(settings.database_path).parent / "skills.jsonl")
                skill_results = skill_store.search_skills(query)
                results["skills"] = skill_results[:limit]
            except Exception:
                results["skills"] = []
        # Memories v2
        if "memories" in srcs:
            try:
                from app.config import settings
                from app.tools.workspace import current_employee_key
                from pathlib import Path
                from app.memory.store_v2 import MemoryStoreV2
                if settings.feature_memory_v2:
                    key = current_employee_key()
                    if key:
                        root = Path(settings.database_path).parent / "employees" / key
                        mem_store = MemoryStoreV2(root / "memory_v2.json")
                    else:
                        mem_store = MemoryStoreV2(Path(settings.database_path).parent / "memory_v2.json")
                    mem_entries = mem_store.entries()
                    mem_results = [e for e in mem_entries if query.lower() in e.text.lower()]
                    results["memories"] = mem_results[:limit]
            except Exception:
                results["memories"] = []
        return json.dumps(results)

    registry.register(
        ToolDefinition(
            name="session_search",
            description=(
                "Full-text search across conversation history, skills, and memories. "
                "Specify sources to limit scope: conversations, skills, memories. "
                "Returns matching items with metadata."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=SessionSearchInput,
            destructive=False,
            employee_eligible=True,
            handler=session_search,
        )
    )
