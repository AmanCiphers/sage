"""Audio bus: broadcasts WAV audio frames to subscribed WebSocket clients."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass
class AudioDelivery:
    """Result of sending one WAV frame to the connected clients."""

    audio_id: str | None
    delivered: int
    heard: bool | None


class AudioBus:
    """Broadcasts WAV audio frames to subscribed WebSocket clients.

    Playback is acknowledged by the browser: each frame is preceded by a JSON
    metadata message carrying an ``id``; clients reply with
    ``{"type": "audio_played", "id": ...}`` (or ``audio_failed``). ``broadcast``
    can wait for that acknowledgement so callers can report whether the user
    actually heard the audio instead of assuming delivery equals success.
    """

    def __init__(self) -> None:
        self._sinks: set[WebSocket] = set()
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._last_broadcast_at: float | None = None
        self._last_error: str | None = None

    @property
    def sink_count(self) -> int:
        return len(self._sinks)

    def subscribe(self, ws: WebSocket) -> None:
        """Register a WebSocket as an audio sink."""
        self._sinks.add(ws)

    def unsubscribe(self, ws: WebSocket) -> None:
        """Unregister a WebSocket."""
        self._sinks.discard(ws)

    def note_error(self, message: str) -> None:
        self._last_error = message

    def confirm(self, audio_id: str, ok: bool, error: str | None = None) -> None:
        """Resolve a pending playback acknowledgement."""
        future = self._pending.get(audio_id)
        if future is not None and not future.done():
            future.set_result(ok)
        if not ok:
            self._last_error = error or "Playback failed on client"

    def status(self) -> dict:
        return {
            "audio_clients": len(self._sinks),
            "pending_confirmations": len(self._pending),
            "last_broadcast_at": self._last_broadcast_at,
            "last_error": self._last_error,
        }

    async def broadcast(
        self,
        wav: bytes,
        *,
        expect_ack: bool = False,
        timeout: float = 3.0,
    ) -> AudioDelivery:
        """Send WAV audio to all subscribed clients; prune dead connections."""
        audio_id = uuid.uuid4().hex
        if not self._sinks:
            self._last_error = "No audio client connected"
            return AudioDelivery(audio_id=None, delivered=0, heard=False if expect_ack else None)

        waiter: asyncio.Future[bool] | None = None
        if expect_ack:
            waiter = asyncio.get_running_loop().create_future()
            self._pending[audio_id] = waiter

        meta = json.dumps(
            {"type": "audio", "id": audio_id, "bytes": len(wav)}
        )
        dead: set[WebSocket] = set()
        delivered = 0
        for ws in list(self._sinks):
            try:
                await ws.send_text(meta)
                await ws.send_bytes(wav)
                delivered += 1
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._sinks.discard(ws)

        self._last_broadcast_at = time.time()

        if delivered == 0:
            self._last_error = "No audio client connected"
            if waiter is not None:
                self._pending.pop(audio_id, None)
            return AudioDelivery(audio_id=None, delivered=0, heard=False if expect_ack else None)

        heard: bool | None = None
        if waiter is not None:
            try:
                heard = await asyncio.wait_for(waiter, timeout=timeout)
            except TimeoutError:
                heard = False
                self._last_error = "Playback not confirmed by client"
            finally:
                self._pending.pop(audio_id, None)
            if heard:
                self._last_error = None

        return AudioDelivery(audio_id=audio_id, delivered=delivered, heard=heard)
