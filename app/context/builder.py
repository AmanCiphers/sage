"""Builds the API message history for each turn.

Assembles: system prompt + optional summary row + token-budgeted tail of
recent raw messages. The summary lives in session state, not in the raw
message table, so raw history stays permanent (ArchitectureV2 §30).
"""

from app.context.compressor import ContextCompressor, summary_row
from app.memory.store import MemoryStore
from app.persistence.store import MessageStore, SessionStateStore


class ContextBuilder:
    def __init__(
        self,
        message_store: MessageStore,
        session_state: SessionStateStore,
        compressor: ContextCompressor,
        system_prompt: str,
        history_fetch_limit: int = 400,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.message_store = message_store
        self.session_state = session_state
        self.compressor = compressor
        self.system_prompt = system_prompt
        self.history_fetch_limit = history_fetch_limit
        self.memory_store = memory_store

    async def build(self) -> list[dict]:
        state = await self.session_state.get()
        rows = await self.message_store.recent(self.history_fetch_limit)

        messages = [self._to_api_message(row) for row in rows]
        tail = self.compressor.extract_tail(messages)

        history: list[dict] = [
            {"role": "system", "content": self._system_content()},
        ]

        if state["summary"]:
            history.append(summary_row(state["summary"]))

        history.extend(tail)

        return history

    def _system_content(self) -> str:
        """System prompt + bounded memory snapshot (name excluded)."""

        if self.memory_store is None:
            return self.system_prompt

        memory = self.memory_store.load()

        if not memory:
            return self.system_prompt

        # Filter out the user's name from the injected snapshot —
        # the model must use "Sir" instead. The name remains in the
        # memory file for factual "what is my name?" queries.
        lines = []
        for line in memory.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("name:") or stripped.lower().startswith("- name:"):
                continue
            lines.append(line)

        filtered = "\n".join(lines) if lines else "(no additional facts)"
        return f"{self.system_prompt}\n\nKnown facts about the user (use when relevant):\n{filtered}"  # noqa: E501

    @staticmethod
    def _to_api_message(row: dict) -> dict:
        message: dict = {"role": row["role"]}

        if row["content"] is not None:
            message["content"] = row["content"]
        elif row["role"] == "assistant":
            message["content"] = None

        if row["tool_calls"]:
            message["tool_calls"] = row["tool_calls"]

        if row["tool_call_id"]:
            message["tool_call_id"] = row["tool_call_id"]

        return message
