"""Cross-encoder reranker provider for RAG reranking."""

from __future__ import annotations

import logging
from typing import List, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

try:
    from sentence_transformers import CrossEncoder
    _HAS_SENTENCE_TRANSFORMERS = True
except Exception:
    _HAS_SENTENCE_TRANSFORMERS = False
    CrossEncoder = None  # type: ignore


class RerankerProvider:
    """Cross-encoder reranker for RAG results."""

    def __init__(self, model_name: str | None = None, batch_size: int | None = None) -> None:
        if not _HAS_SENTENCE_TRANSFORMERS:
            raise RuntimeError("sentence-transformers not installed; cannot use reranker")
        model_name = model_name or settings.rag_rerank_model
        self.model = CrossEncoder(model_name)
        self.batch_size = batch_size or settings.rag_rerank_batch_size

    def rerank(self, query: str, candidates: List[Tuple[str, float, str]], top_n: int | None = None) -> List[Tuple[str, float, str]]:
        """Rerank candidates using cross-encoder.

        candidates: list of (msg_id, score, content)
        Returns reranked list sorted by cross-encoder score descending, limited to top_n.
        """
        if not candidates:
            return []

        top_n = top_n or settings.rag_rerank_top_n
        # Prepare pairs for cross-encoder
        pairs = [(query, cand[2]) for cand in candidates]
        # Predict scores in batches
        scores = self.model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        # Combine
        reranked = []
        for (msg_id, _orig_score, content), score in zip(candidates, scores):
            reranked.append((msg_id, float(score), content))
        # Sort by cross-encoder score descending
        reranked.sort(key=lambda x: x[1], reverse=True)
        return reranked[:top_n]


def create_reranker() -> RerankerProvider | None:
    """Factory to create reranker if enabled and available."""
    if not settings.rag_rerank_enabled:
        return None
    if not _HAS_SENTENCE_TRANSFORMERS:
        logger.warning("reranker requested but sentence-transformers not installed")
        return None
    try:
        return RerankerProvider()
    except Exception as e:
        logger.warning("Failed to init reranker: %s", e)
        return None