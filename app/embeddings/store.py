"""Embedding store: SQLite persistence + cosine similarity search."""

from __future__ import annotations

from app.embeddings.provider import EmbeddingProvider
from app.persistence.database import Database


class EmbeddingStore:
    """Store and search embeddings in SQLite."""

    def __init__(
        self,
        database: Database,
        provider: EmbeddingProvider,
    ) -> None:
        self.db = database
        self.provider = provider
        self.dim = provider.dim
        self.model = provider.model

    async def save(self, message_id: str, embedding: list[float]) -> None:
        """Save embedding for a message."""
        blob = self.provider.serialize(embedding)
        await self.db.execute(
            """
            INSERT OR REPLACE INTO message_embeddings
            (message_id, embedding, model, dim)
            VALUES (?, ?, ?, ?)
            """,
            (message_id, blob, self.model, self.dim),
        )
        await self.db.commit()

    async def get(self, message_id: str) -> list[float] | None:
        """Get embedding for a message."""
        cursor = await self.db.execute(
            "SELECT embedding FROM message_embeddings WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self.provider.deserialize(row[0], self.dim)

    async def has_embedding(self, message_id: str) -> bool:
        """Check if message has embedding."""
        cursor = await self.db.execute(
            "SELECT 1 FROM message_embeddings WHERE message_id = ?",
            (message_id,),
        )
        return (await cursor.fetchone()) is not None

    async def delete(self, message_id: str) -> None:
        """Delete embedding for a message."""
        await self.db.execute(
            "DELETE FROM message_embeddings WHERE message_id = ?",
            (message_id,),
        )
        await self.db.commit()

    async def search(
        self,
        query_embedding: list[float],
        k: int,
        exclude_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """
        Cosine similarity search.
        Returns list of (message_id, similarity) sorted by similarity desc.
        """
        # Get all embeddings for this model
        cursor = await self.db.execute(
            "SELECT message_id, embedding FROM message_embeddings WHERE model = ?",
            (self.model,),
        )
        rows = await cursor.fetchall()

        if not rows:
            return []

        exclude = exclude_ids or set()
        query_norm = self._norm(query_embedding)

        results = []
        for msg_id, blob in rows:
            if msg_id in exclude:
                continue
            try:
                emb = self.provider.deserialize(blob, self.dim)
                emb_norm = self._norm(emb)
                if emb_norm == 0 or query_norm == 0:
                    continue
                # Cosine similarity
                sim = sum(
                    a * b for a, b in zip(query_embedding, emb, strict=True)
                ) / (query_norm * emb_norm)
                results.append((msg_id, sim))
            except Exception:
                continue

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:k]

    @staticmethod
    def _norm(vec: list[float]) -> float:
        return sum(x * x for x in vec) ** 0.5

    async def get_all_ids(self) -> list[str]:
        """Get all message IDs with embeddings."""
        cursor = await self.db.execute(
            "SELECT message_id FROM message_embeddings WHERE model = ?",
            (self.model,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def count(self) -> int:
        """Count embeddings for this model."""
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM message_embeddings WHERE model = ?",
            (self.model,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
