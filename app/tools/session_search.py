"""The `session_search` tool: full-text search over raw conversation history.

SQLite FTS5 over the messages table. Lets SAGE recall anything from the
permanent raw history even after compaction removed it from the context.
"""

import json

from app.persistence.store import MessageStore
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput


class SessionSearchInput(ToolInput):
    query: str
    limit: int = 5


def register_session_search(registry, message_store: MessageStore) -> None:
    async def session_search(query: str, limit: int = 5) -> str:
        return json.dumps(
            {"results": await message_store.search(query, limit)}
        )

    registry.register(
        ToolDefinition(
            name="session_search",
            description=(
                "Full-text search across the entire conversation history, "
                "including parts no longer in the context window. "
                "Returns matching messages with id, role, and content."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=SessionSearchInput,
            destructive=False,
            employee_eligible=True,
            handler=session_search,
        )
    )
