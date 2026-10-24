"""Builds the API message history for each turn.

Assembles: system prompt + optional summary row + RAG context + token-budgeted tail of
recent raw messages. The summary lives in session state, not in the raw
message table, so raw history stays permanent (ArchitectureV2 §30).
"""

import logging

from app.config import settings
from app.context.compressor import ContextCompressor, summary_row
from app.embeddings import EmbeddingProvider, EmbeddingStore
from app.memory.store import MemoryStore
from app.persistence.store import MessageStore, SessionStateStore

logger = logging.getLogger(__name__)


class ContextBuilder:
    def __init__(
        self,
        message_store: MessageStore,
        session_state: SessionStateStore,
        compressor: ContextCompressor,
        system_prompt: str,
        history_fetch_limit: int = 400,
        memory_store: MemoryStore | None = None,
        embedding_store: EmbeddingStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        rag_enabled: bool = True,
    ) -> None:
        self.message_store = message_store
        self.session_state = session_state
        self.compressor = compressor
        self.system_prompt = system_prompt
        self.history_fetch_limit = history_fetch_limit
        self.memory_store = memory_store
        self.embedding_store = embedding_store
        self.embedding_provider = embedding_provider
        self.rag_enabled = rag_enabled and settings.rag_enabled
        self.logger = logger

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

        # RAG auto-injection: hybrid FTS5 + embedding retrieval
        if self.rag_enabled and self.embedding_store and self.embedding_provider:
            last_user_msg = self._get_last_user_message(messages)
            if last_user_msg:
                rag_context = await self._build_rag_context(last_user_msg)
                if rag_context:
                    history.append({"role": "system", "content": rag_context})

        history.extend(tail)

        return history

    def _get_last_user_message(self, messages: list[dict]) -> str | None:
        for m in reversed(messages):
            if m["role"] == "user" and m.get("content"):
                return m["content"]
        return None

    async def _build_rag_context(self, query: str) -> str | None:
        # 1. FTS5 search (exact keywords)
        fts_results = []
        fts_scores = {}
        try:
            fts_results = await self.message_store.search(query, limit=15)
            fts_scores = {str(r["id"]): r.get("score", 1.0) for r in fts_results}
        except Exception as e:
            self.logger.warning("fts5_search_failed error=%s", e)

        # 2. Embedding search (semantic)
        emb_scores = {}
        try:
            query_emb = await self.embedding_provider.embed_query(query)
            emb_results = await self.embedding_store.search(
                query_emb, k=15, exclude_ids=set()
            )
            emb_scores = {msg_id: score for msg_id, score in emb_results}
        except Exception as e:
            self.logger.warning("embedding_search_failed error=%s", e)

        # If both fail, skip RAG
        if not fts_results and not emb_scores:
            return None

        # 3. Merge & rerank
        merged = self._merge_results(fts_results, emb_scores, fts_scores)
        if not merged:
            return None

        # 4. Format as context
        parts = ["Relevant context from conversation history:"]
        for _msg_id, score, content in merged[: settings.rag_top_k]:
            parts.append(f"[score={score:.3f}] {content[:500]}")

        return "\n".join(parts)

    def _merge_results(
        self,
        fts_results: list[dict],
        emb_scores: dict[str, float],
        fts_scores: dict[str, float],
    ) -> list[tuple[str, float, str]]:
        """Merge FTS5 and embedding results with weighted scoring."""
        # Normalize scores to 0-1
        max_fts = max(fts_scores.values()) if fts_scores else 1.0
        max_emb = max(emb_scores.values()) if emb_scores else 1.0

        combined: dict[str, tuple[float, str]] = {}

        # FTS5 results
        for r in fts_results:
            msg_id = str(r["id"])
            fts_score = fts_scores.get(msg_id, 0) / max_fts
            content = r.get("content", "")
            if content:
                combined[msg_id] = (fts_score, content)

        # Embedding results
        for msg_id, emb_score in emb_scores.items():
            norm_emb = emb_score / max_emb if max_emb > 0 else 0
            if msg_id in combined:
                fts_score, content = combined[msg_id]
                final = (
                    settings.rag_fts_weight * fts_score
                    + settings.rag_embedding_weight * norm_emb
                )
                combined[msg_id] = (final, content)
            else:
                content = ""
                # We don't have content from embedding store, skip
                # In practice, we'd fetch from messages table
                continue

        # Sort by final score
        sorted_items = sorted(combined.items(), key=lambda x: x[1][0], reverse=True)

        # Filter by threshold
        filtered = [
            (msg_id, score, content)
            for msg_id, (score, content) in sorted_items
            if score >= settings.rag_similarity_threshold
        ]

        return filtered

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
