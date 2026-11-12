"""Voice-activity detection.

Default is a lightweight energy-based VAD (zero download, negligible CPU).
Silero VAD is used when the ONNX model file is present at
``voice_model_dir/silero_vad.onnx`` and ``voice_use_silero`` is on.
"""

from __future__ import annotations

import numpy as np

from app.voice.audio import pcm16_to_float
from app.voice.config import VoiceConfig


class EnergyVAD:
    """Simple RMS-threshold VAD over 16-bit PCM frames."""

    def __init__(self, threshold: float = 0.02) -> None:
        self._threshold = threshold

    def is_speech(self, frame: bytes) -> bool:
        arr = pcm16_to_float(frame)
        if arr.size == 0:
            return False
        rms = float(np.sqrt(float(np.mean(arr**2))))
        return rms > self._threshold

    def reset(self) -> None:
        pass


class SileroVAD:
    """Silero VAD via onnxruntime (loaded lazily, CPUExecutionProvider)."""

    def __init__(self, model_path, threshold: float = 0.5) -> None:
        self._path = model_path
        self._threshold = threshold
        self._session = None
        self._h = None
        self._c = None

    def _load(self) -> None:
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(self._path), providers=["CPUExecutionProvider"]
        )
        import numpy as np

        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def is_speech(self, frame: bytes) -> bool:
        if self._session is None:
            self._load()

        audio = pcm16_to_float(frame)
        if audio.size == 0:
            return False
        audio = audio.reshape(1, -1)
        inputs = {self._session.get_inputs()[0].name: audio}
        for inp in self._session.get_inputs()[1:]:
            if "h" in inp.name:
                inputs[inp.name] = self._h
            elif "c" in inp.name:
                inputs[inp.name] = self._c
        outputs = self._session.run(None, inputs)
        prob = outputs[0][0][0]
        if len(outputs) > 1 and outputs[1] is not None:
            self._h, self._c = outputs[1], outputs[2]
        return float(prob) > self._threshold

    def reset(self) -> None:
        self._h = None
        self._c = None


def make_vad(cfg: VoiceConfig):
    if cfg.use_silero:
        model_path = cfg.model_dir / "silero_vad.onnx"
        if model_path.exists():
            return SileroVAD(model_path, threshold=cfg.vad_threshold)
    return EnergyVAD(threshold=cfg.vad_threshold)
