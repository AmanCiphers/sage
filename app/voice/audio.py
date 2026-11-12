"""PCM<->float helpers and WAV encoding (16 kHz mono 16-bit)."""

from __future__ import annotations

import io
import wave

import numpy as np


def pcm16_to_float(data: bytes) -> np.ndarray:
    """Decode raw signed 16-bit PCM into float32 in [-1, 1]."""
    if not data:
        return np.zeros(0, dtype=np.float32)
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return arr


def float_to_pcm16(arr: np.ndarray) -> bytes:
    """Encode float32 audio into raw signed 16-bit PCM bytes."""
    a = np.clip(arr, -1.0, 1.0)
    return (a * 32767).astype(np.int16).tobytes()


def pcm_to_wav(data: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM into a WAV container for playback/consumption."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data)
    return buf.getvalue()
