"""Wake-word detection via openWakeWord (opt-in).

Off by default to keep idle CPU low (push-to-talk is the primary trigger).
openWakeWord ships pretrained keywords; a custom "sage" model is a follow-up,
so the active keyword is configurable (default ``hey_jarvis``).
"""

from __future__ import annotations

import numpy as np

from app.voice.audio import pcm16_to_float
from app.voice.config import VoiceConfig


class OpenWakeWordDetector:
    def __init__(self, keyword: str, threshold: float = 0.5) -> None:
        self._keyword = keyword
        self._threshold = threshold
        self._model = None

    def _load(self) -> None:
        import openwakeword

        self._model = openwakeword.Model()

    def feed(self, frame: bytes) -> bool:
        if self._model is None:
            try:
                self._load()
            except Exception:
                return False
        audio = pcm16_to_float(frame)
        if audio.size == 0:
            return False
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        try:
            pred = self._model.predict(pcm)
        except Exception:
            return False
        for name, score in pred.items():
            if self._keyword in name and float(score) > self._threshold:
                self._model.reset()
                return True
        return False

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()


class DisabledWakeWord:
    def feed(self, frame: bytes) -> bool:
        return False

    def reset(self) -> None:
        pass


def make_wakeword(cfg: VoiceConfig):
    if cfg.wake_enabled:
        return OpenWakeWordDetector(cfg.wake_keyword)
    return DisabledWakeWord()
