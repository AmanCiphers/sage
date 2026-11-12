"""VoiceSession: buffer audio, detect trigger, transcribe, optionally run a turn.

Pipeline: frames -> VAD/wake word -> ASR -> transcript (-> optional turn).

The session is transport-agnostic: it reports events (state, transcript,
response, error) through callbacks, and the WebSocket gateway
bridges those callbacks to the browser. With ``turn_handler=None`` the
session is transcription-only; the browser then drives the chat turn over
the normal streaming HTTP path.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.voice.config import VoiceConfig
from app.voice.interfaces import ASR, VAD, WakeWord

TurnHandler = Callable[[str], Awaitable[str]]


@dataclass
class VoiceEvents:
    state: Callable[[str], None]
    transcript: Callable[[str], None]
    response: Callable[[str], None]
    error: Callable[[str], None]


class VoiceSession:
    def __init__(
        self,
        vad: VAD,
        asr: ASR,
        wake: WakeWord,
        turn_handler: TurnHandler | None,
        cfg: VoiceConfig,
        events: VoiceEvents,
    ) -> None:
        self._vad = vad
        self._asr = asr
        self._wake = wake
        self._turn_handler = turn_handler
        self._cfg = cfg
        self._events = events

        self.mode = "ptt"
        self._frames: list[bytes] = []
        self._ptt = False
        self._busy = False
        self._last_speech = 0.0
        self._started = 0.0
        self._task = None

    # ---- control -----------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode in ("ptt", "wake"):
            self.mode = mode
        self._reset_turn()
        self._events.state("idle")

    def ptt_start(self) -> None:
        if self._busy:
            return
        self._ptt = True
        self._frames = []
        self._started = time.monotonic()
        self._last_speech = time.monotonic()
        self._events.state("listening")

    def ptt_end(self) -> None:
        if not self._ptt or self._busy:
            return
        self._ptt = False
        self._schedule_process()

    def cancel(self) -> None:
        self._ptt = False
        self._frames = []
        self._busy = False
        self._events.state("idle")

    # ---- streaming frames --------------------------------------------------

    def feed(self, frame: bytes) -> None:
        if self.mode == "wake" and not self._ptt:
            if self._wake.feed(frame):
                self.ptt_start()
            return

        if not self._ptt:
            return

        if len(self._frames) >= self._cfg.max_turn_frames:
            self._frames.append(frame)
            self.ptt_end()
            return

        self._frames.append(frame)
        if self._vad.is_speech(frame):
            self._last_speech = time.monotonic()

        if self.mode == "wake":
            # In wake mode, auto-finalize after a trailing silence.
            silence = time.monotonic() - self._last_speech
            if silence > self._cfg.silence_ms / 1000:
                self.ptt_end()

    # ---- turn execution ----------------------------------------------------

    def _schedule_process(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._events.state("processing")
        self._task = asyncio.create_task(self._process())

    async def _process(self) -> None:
        try:
            frames = self._frames
            self._frames = []
            audio = b"".join(frames)
            if not audio:
                return
            text = await self._asr.transcribe(audio, self._cfg.sample_rate)
            if not text.strip():
                return
            self._events.transcript(text.strip())
            if self._turn_handler is None:
                return
            full = await self._turn_handler(text.strip())
            self._events.response(full or "")
        except Exception as exc:
            self._events.error(str(exc))
        finally:
            self._busy = False
            self._events.state("idle")

    def _reset_turn(self) -> None:
        self._ptt = False
        self._frames = []
        self._busy = False
