"""Swappable engine interfaces for the voice pipeline.

Engines are Protocols so the real implementations (faster-whisper, Chatterbox,
Piper, Silero, openWakeWord) can be replaced by fakes in tests and by other
backends later without touching the session logic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class VAD(Protocol):
    """Voice-activity detection over a single 16-bit PCM frame."""

    def is_speech(self, frame: bytes) -> bool: ...

    def reset(self) -> None: ...


@runtime_checkable
class ASR(Protocol):
    """Speech-to-text over raw 16-bit PCM audio."""

    async def transcribe(self, audio: bytes, sample_rate: int) -> str: ...

    async def close(self) -> None: ...


@runtime_checkable
class TTS(Protocol):
    """Text-to-speech returning WAV audio bytes."""

    async def synthesize(self, text: str) -> bytes: ...


@runtime_checkable
class WakeWord(Protocol):
    """Streaming wake-word detection over 16-bit PCM frames."""

    def feed(self, frame: bytes) -> bool: ...

    def reset(self) -> None: ...
