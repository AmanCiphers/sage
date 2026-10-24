#!/usr/bin/env python3
"""Backfill embeddings for existing messages.

Run with: python scripts/backfill_embeddings.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.embeddings import EmbeddingIndexer, create_primary_provider
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import MessageStore
from app.embeddings import EmbeddingStore


async def main() -> None:
    print("Starting embedding backfill...")
    print(f"Database: {settings.database_path}")

    # Create primary embedding provider
    provider = create_primary_provider()
    if not provider:
        print("ERROR: No embedding provider configured (need NEBIUS_API_KEY or NVIDIA_API_KEY)")
        return

    print(f"Using provider: {provider.model} (dim={provider.dim})")

    # Open main database
    db = Database(settings.database_path)
    await db.connect()
    await initialize_schema(db)

    message_store = MessageStore(db)
    embedding_store = EmbeddingStore(db, provider)

    indexer = EmbeddingIndexer(
        message_store=message_store,
        embedding_store=embedding_store,
        provider=provider,
        batch_size=50,
    )

    print("Backfilling embeddings for existing messages...")
    try:
        count = await indexer.backfill_all(batch_size=50)
        print(f"Backfill complete! Indexed {count} messages.")
    except Exception as e:
        print(f"Backfill failed: {e}")
        raise
    finally:
        await provider.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())