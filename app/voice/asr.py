"""Speech-to-text via faster-whisper (CTranslate2).

The model loads lazily on first use and runs in a worker thread so the event
loop never blocks. ``base.en`` + ``int8`` keeps CPU/RAM usage low on
Apple-Silicon/CPU.
"""

from __future__ import annotations

import asyncio
import threading

from app.voice.audio import pcm16_to_float
from app.voice.config import VoiceConfig


class FasterWhisperASR:
    def __init__(
        self,
        model: str,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self._model_name,
            device=self._device,
            compute_type=self._compute_type,
        )

    def _transcribe_sync(self, audio: bytes, sample_rate: int) -> str:
        with self._lock:
            if self._model is None:
                self._load()
            model = self._model
        arr = pcm16_to_float(audio)
        if arr.size == 0:
            return ""
        language = "en" if self._model_name.endswith(".en") else None
        segments, _ = model.transcribe(arr, beam_size=1, language=language)
        return "".join(seg.text for seg in segments).strip()

    async def transcribe(self, audio: bytes, sample_rate: int) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio, sample_rate)

    async def close(self) -> None:
        with self._lock:
            self._model = None


def make_asr(cfg: VoiceConfig) -> FasterWhisperASR:
    return FasterWhisperASR(
        model=cfg.asr_model,
        device=cfg.asr_device,
        compute_type=cfg.asr_compute_type,
    )
