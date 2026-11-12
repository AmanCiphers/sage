"""Voice configuration, derived from the SAGE settings block."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class VoiceConfig:
    sample_rate: int
    frame_ms: int
    silence_ms: int
    max_turn_seconds: float
    asr_model: str
    asr_device: str
    asr_compute_type: str
    vad_threshold: float
    use_silero: bool
    wake_enabled: bool
    wake_keyword: str
    chatterbox_url: str
    tts_primary: str
    tts_fallback: str
    piper_voice: str
    model_dir: Path
    tts_client_chunking: bool = True
    tts_chunk_chars: int = 220
    tts_prefetch: int = 2
    tts_timeout_seconds: float = 30.0

    @property
    def frame_bytes(self) -> int:
        """Bytes per 16-bit mono PCM frame at the configured frame length."""
        return self.sample_rate * 2 * self.frame_ms // 1000

    @property
    def max_turn_frames(self) -> int:
        return max(1, int(self.max_turn_seconds * 1000 / self.frame_ms))


def get_voice_config() -> VoiceConfig:
    return VoiceConfig(
        sample_rate=settings.voice_sample_rate,
        frame_ms=settings.voice_frame_ms,
        silence_ms=settings.voice_silence_ms,
        max_turn_seconds=settings.voice_max_turn_seconds,
        asr_model=settings.voice_asr_model,
        asr_device=settings.voice_asr_device,
        asr_compute_type=settings.voice_asr_compute_type,
        vad_threshold=settings.voice_vad_threshold,
        use_silero=settings.voice_use_silero,
        wake_enabled=settings.voice_wake_enabled,
        wake_keyword=settings.voice_wake_keyword,
        chatterbox_url=settings.voice_chatterbox_url,
        tts_primary=settings.voice_tts_primary,
        tts_fallback=settings.voice_tts_fallback,
        piper_voice=settings.voice_piper_voice,
        model_dir=settings.voice_model_dir,
        tts_client_chunking=settings.voice_tts_client_chunking,
        tts_chunk_chars=settings.voice_tts_chunk_chars,
        tts_prefetch=settings.voice_tts_prefetch,
        tts_timeout_seconds=settings.voice_tts_timeout_seconds,
    )
