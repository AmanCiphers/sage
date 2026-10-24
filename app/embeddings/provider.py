"""Embedding provider for NVIDIA and Nebius (OpenAI-compatible APIs)."""

from __future__ import annotations

import struct
from typing import Any

import httpx

from app.config import settings


class EmbeddingProvider:
    """OpenAI-compatible embedding provider (NVIDIA/Nebius)."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dim: int,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self._client = httpx.AsyncClient(timeout=timeout)
        self._is_nvidia = "nvidia" in base_url or "nvidia" in model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def embed_query(self, text: str) -> list[float]:
        """Embed a query (uses input_type='query' for NVIDIA)."""
        return await self._embed(text, input_type="query")

    async def embed_passage(self, text: str) -> list[float]:
        """Embed a passage/document (uses input_type='passage' for NVIDIA)."""
        return await self._embed(text, input_type="passage")

    async def _embed(self, text: str, input_type: str) -> list[float]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": text,
            "encoding_format": "float",
        }

        # NVIDIA models require input_type parameter
        if self._is_nvidia:
            payload["input_type"] = input_type

        # Nebius supports dimensions parameter for Matryoshka
        if not self._is_nvidia:
            payload["dimensions"] = self.dim

        url = f"{self.base_url}/embeddings"

        response = await self._client.post(
            url,
            json=payload,
            headers=self._headers(),
        )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Embedding API error {response.status_code}: {response.text}"
            )

        data = response.json()
        embedding = data["data"][0]["embedding"]

        # Truncate/pad to expected dimension if needed
        if len(embedding) != self.dim:
            if len(embedding) > self.dim:
                embedding = embedding[: self.dim]
            else:
                embedding = embedding + [0.0] * (self.dim - len(embedding))

        return embedding

    async def embed_batch(
        self, texts: list[str], input_type: str = "passage"
    ) -> list[list[float]]:
        """Embed multiple texts in one request (for indexing)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }

        if self._is_nvidia:
            payload["input_type"] = input_type
        else:
            payload["dimensions"] = self.dim

        url = f"{self.base_url}/embeddings"

        response = await self._client.post(
            url,
            json=payload,
            headers=self._headers(),
        )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Embedding batch API error {response.status_code}: {response.text}"
            )

        data = response.json()
        embeddings = [item["embedding"] for item in data["data"]]

        # Normalize dimensions
        result = []
        for emb in embeddings:
            if len(emb) != self.dim:
                emb = (
                    emb[: self.dim]
                    if len(emb) > self.dim
                    else emb + [0.0] * (self.dim - len(emb))
                )
            result.append(emb)

        return result

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def serialize(embedding: list[float]) -> bytes:
        """Serialize float32 list to bytes for SQLite BLOB storage."""
        return struct.pack(f"{len(embedding)}f", *embedding)

    @staticmethod
    def deserialize(data: bytes, dim: int) -> list[float]:
        """Deserialize bytes to float32 list."""
        return list(struct.unpack(f"{dim}f", data))


def create_primary_provider() -> EmbeddingProvider | None:
    """Create primary embedding provider (NVIDIA free tier)."""
    if not settings.nvidia_api_key:
        return None
    return EmbeddingProvider(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        model=settings.rag_embedding_model,
        dim=settings.rag_embedding_dim,
    )


def create_fallback_provider() -> EmbeddingProvider | None:
    """Create fallback embedding provider (Nebius)."""
    if not settings.nebius_api_key:
        return None
    return EmbeddingProvider(
        api_key=settings.nebius_api_key,
        base_url=settings.nebius_base_url,
        model=settings.rag_fallback_model,
        dim=settings.rag_embedding_dim,
    )
