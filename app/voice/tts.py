"""Text-to-speech: Chatterbox (local server) primary.

The server chunks long text internally and returns one merged WAV. To start
speaking sooner, we also split the text here and request the pieces separately,
broadcasting each WAV as soon as it is ready. ``synthesize`` remains available
for callers that need a single WAV.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave
from collections.abc import AsyncIterator

import httpx

from app.voice.config import VoiceConfig

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split an over-long sentence, preferring whitespace boundaries."""
    pieces: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def chunk_text(text: str, max_chars: int = 220) -> list[str]:
    """Split text into sentence-aware chunks of at most ``max_chars``."""
    text = " ".join((text or "").split())
    if not text:
        return []
    max_chars = max(1, int(max_chars))

    chunks: list[str] = []
    current = ""
    for sentence in (s.strip() for s in _SENTENCE_SPLIT.split(text)):
        if not sentence:
            continue
        for piece in _hard_split(sentence, max_chars):
            candidate = f"{current} {piece}" if current else piece
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _looks_like_wav(data: bytes) -> bool:
    return len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def merge_wavs(parts: list[bytes]) -> bytes:
    """Merge several PCM WAVs into one, keeping the first part's format."""
    if not parts:
        return b""
    if len(parts) == 1:
        return parts[0]

    with wave.open(io.BytesIO(parts[0]), "rb") as first:
        channels = first.getnchannels()
        width = first.getsampwidth()
        rate = first.getframerate()
        frames = first.readframes(first.getnframes())

    extra: list[bytes] = []
    for part in parts[1:]:
        try:
            with wave.open(io.BytesIO(part), "rb") as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (
                    channels,
                    width,
                    rate,
                ):
                    logger.warning("tts_merge_format_mismatch; skipping part")
                    continue
                extra.append(w.readframes(w.getnframes()))
        except Exception as exc:
            logger.warning("tts_merge_bad_part error=%s", exc)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames + b"".join(extra))
    return buf.getvalue()


class ChatterboxTTS:
    def __init__(
        self,
        url: str,
        *,
        client_chunking: bool = True,
        chunk_chars: int = 220,
        prefetch: int = 2,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._client_chunking = client_chunking
        self._chunk_chars = chunk_chars
        self._prefetch = max(1, prefetch)
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    @property
    def url(self) -> str:
        return self._url

    async def _synthesize_one(self, text: str) -> bytes:
        resp = await self._client.post(self._url, json={"text": text})
        resp.raise_for_status()
        data = resp.content
        if not _looks_like_wav(data):
            raise ValueError("TTS server returned a non-WAV response")
        return data

    async def synthesize_chunks(self, text: str) -> AsyncIterator[bytes]:
        """Yield WAV audio per chunk, in order, with bounded prefetch."""
        text = (text or "").strip()
        if not text:
            return

        chunks = (
            chunk_text(text, self._chunk_chars)
            if self._client_chunking
            else [text]
        )
        chunks = [c for c in chunks if c.strip()]
        if not chunks:
            return

        pending: dict[int, asyncio.Task[bytes]] = {}
        next_index = 0
        try:
            for index in range(len(chunks)):
                while next_index < len(chunks) and len(pending) < self._prefetch:
                    pending[next_index] = asyncio.create_task(
                        self._synthesize_one(chunks[next_index])
                    )
                    next_index += 1
                wav = await pending.pop(index)
                yield wav
        finally:
            for task in pending.values():
                task.cancel()
            if pending:
                await asyncio.gather(*pending.values(), return_exceptions=True)

    async def synthesize(self, text: str) -> bytes:
        parts = [part async for part in self.synthesize_chunks(text)]
        if not parts:
            return b""
        return merge_wavs(parts)

    async def close(self) -> None:
        await self._client.aclose()


def make_tts(cfg: VoiceConfig):
    """Return a ChatterboxTTS instance; raise if configuration missing."""
    url = cfg.chatterbox_url
    if not url:
        raise RuntimeError("Chatterbox TTS URL not configured")
    return ChatterboxTTS(
        url,
        client_chunking=cfg.tts_client_chunking,
        chunk_chars=cfg.tts_chunk_chars,
        prefetch=cfg.tts_prefetch,
        timeout=cfg.tts_timeout_seconds,
    )
