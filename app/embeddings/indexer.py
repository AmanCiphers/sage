"""Embedding indexer: background indexing of messages."""

from __future__ import annotations

import asyncio

from app.embeddings.provider import EmbeddingProvider
from app.embeddings.store import EmbeddingStore
from app.observability.logging import get_logger
from app.persistence.store import MessageStore

logger = get_logger(__name__)


class EmbeddingIndexer:
    """Background indexer for message embeddings."""

    def __init__(
        self,
        message_store: MessageStore,
        embedding_store: EmbeddingStore,
        provider: EmbeddingProvider,
        batch_size: int = 50,
    ) -> None:
        self.message_store = message_store
        self.embedding_store = embedding_store
        self.provider = provider
        self.batch_size = batch_size

    async def index_new_messages(self, since_id: int = 0) -> int:
        """Index messages that don't have embeddings yet."""
        # Get messages without embeddings
        cursor = await self.message_store.database.execute(
            """
            SELECT m.id, m.content, m.role
            FROM messages m
            LEFT JOIN message_embeddings e ON m.id = e.message_id
            WHERE e.message_id IS NULL
              AND m.id > ?
              AND m.content IS NOT NULL
              AND m.content != ''
            ORDER BY m.id
            LIMIT ?
            """,
            (since_id, self.batch_size),
        )
        rows = await cursor.fetchall()

        if not rows:
            return 0

        indexed = 0
        texts = []
        msg_ids = []

        for msg_id, content, _role in rows:
            if content and content.strip():
                texts.append(content)
                msg_ids.append(str(msg_id))

        if not texts:
            return 0

        try:
            # Embed in batch (use passage type for documents)
            embeddings = await self.provider.embed_batch(texts, input_type="passage")

            # Save all
            for msg_id, emb in zip(msg_ids, embeddings, strict=True):
                await self.embedding_store.save(msg_id, emb)

            indexed = len(msg_ids)
            logger.debug("indexed_batch count=%d", indexed)

        except Exception as e:
            logger.warning("embedding_batch_failed error=%s", e)

        return indexed

    async def backfill_all(self, batch_size: int = 100) -> int:
        """Backfill all existing messages (one-time operation)."""
        self.batch_size = batch_size
        total = 0
        last_id = 0

        while True:
            count = await self.index_new_messages(last_id)
            if count == 0:
                break
            total += count
            last_id = total  # Approximate - messages are sequential

            # Small delay to not overwhelm the API
            await asyncio.sleep(0.1)

        logger.info("embedding_backfill_complete total=%d", total)
        return total

    async def reindex_model(self, old_model: str, new_model: str) -> int:
        """Re-index all messages when embedding model changes."""
        # This would require a more complex implementation
        # For now, just re-index everything
        return await self.backfill_all()
