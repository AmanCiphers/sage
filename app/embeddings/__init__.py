"""Embeddings module: providers, storage, and indexing for RAG."""

from app.embeddings.indexer import EmbeddingIndexer
from app.embeddings.provider import (
    EmbeddingProvider,
    create_fallback_provider,
    create_primary_provider,
)
from app.embeddings.store import EmbeddingStore

__all__ = [
    "EmbeddingIndexer",
    "EmbeddingProvider",
    "EmbeddingStore",
    "create_fallback_provider",
    "create_primary_provider",
]
