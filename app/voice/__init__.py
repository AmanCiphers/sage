"""Voice module: wake word / push-to-talk -> STT -> SAGE -> conversational line -> TTS.

Resource-light by default for Apple-Silicon/CPU: models load lazily and only
during an active turn; push-to-talk is the primary trigger (wake word is
opt-in). Voice is another client of SAGE, not a separate intelligence.
"""

from app.voice.config import VoiceConfig, get_voice_config
from app.voice.gateway import voice_endpoint
from app.voice.session import VoiceEvents, VoiceSession

__all__ = [
    "VoiceConfig",
    "VoiceEvents",
    "VoiceSession",
    "get_voice_config",
    "voice_endpoint",
]
