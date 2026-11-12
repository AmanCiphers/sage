"""Speak tool: lets SAGE synthesize speech via Chatterbox and broadcast to audio sinks."""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.tools.models import ToolCapability, ToolClassification, ToolDefinition
from app.tools.registry import ToolRegistry
from app.voice.audio_bus import AudioBus


class SpeakInput(BaseModel):
    text: str


class VoiceStatusInput(BaseModel):
    pass


# Context variable to track if speak has been called in current turn
_speak_called: contextvars.ContextVar[bool] = contextvars.ContextVar("_speak_called", default=False)


def reset_speak_flag() -> None:
    """Call at the start of each turn to reset the speak flag."""
    _speak_called.set(False)


def speak_was_called() -> bool:
    """Whether the speak tool has already run in the current turn."""
    return _speak_called.get()


def register_speak_tools(
    registry: ToolRegistry,
    tts_getter: Callable[[], Any | None],
    audio_bus: AudioBus,
) -> None:
    async def speak_handler(text: str) -> dict:
        # Prevent multiple speak calls per turn
        if _speak_called.get():
            return {"spoken": False, "error": "Already spoke this turn"}

        tts = tts_getter()
        if tts is None:
            return {"spoken": False, "error": "Voice not enabled or TTS unavailable"}

        text = text.strip() if isinstance(text, str) else str(text).strip()
        if not text:
            return {"spoken": False, "error": "Empty text"}

        chunks = 0
        delivered = 0
        heard: bool | None = None
        try:
            if hasattr(tts, "synthesize_chunks"):
                async for wav in tts.synthesize_chunks(text):
                    delivery = await audio_bus.broadcast(
                        wav, expect_ack=(chunks == 0)
                    )
                    if delivery.delivered == 0:
                        message = "No audio client connected"
                        audio_bus.note_error(message)
                        return {
                            "spoken": False,
                            "error": message,
                            "chunks": chunks,
                        }
                    if not _speak_called.get():
                        _speak_called.set(True)
                    if chunks == 0:
                        heard = delivery.heard
                    delivered = max(delivered, delivery.delivered)
                    chunks += 1
            else:
                wav = await tts.synthesize(text)
                delivery = await audio_bus.broadcast(wav, expect_ack=True)
                if delivery.delivered == 0:
                    message = "No audio client connected"
                    audio_bus.note_error(message)
                    return {"spoken": False, "error": message, "chunks": 0}
                _speak_called.set(True)
                heard = delivery.heard
                delivered = delivery.delivered
                chunks = 1
        except Exception as e:
            message = f"TTS failed: {type(e).__name__}: {e}"
            audio_bus.note_error(message)
            return {
                "spoken": _speak_called.get(),
                "error": message,
                "chunks": chunks,
            }

        if chunks == 0:
            return {"spoken": False, "error": "TTS produced no audio"}

        result = {
            "spoken": True,
            "text": text,
            "chunks": chunks,
            "delivered": delivered,
            "heard": heard is True,
        }
        if heard is not True:
            result["warning"] = "Playback was not confirmed by the browser"
        return result

    def voice_status_handler() -> dict:
        tts = tts_getter()
        return {
            "tts_configured": tts is not None,
            "tts_url": getattr(tts, "url", None),
            **audio_bus.status(),
        }

    registry.register(
        ToolDefinition(
            name="speak",
            description=(
                "Speak text aloud via the TTS system. Use this for anything you want "
                "to say aloud — your spoken reply, announcements, or confirmations. "
                "Speak naturally and concisely. Can only be called once per turn. "
                "The result includes `heard`: true only when the browser confirmed "
                "playback started. Never claim the user heard audio unless "
                "`heard` is true; if it is false, say playback was not confirmed."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=SpeakInput,
            destructive=False,
            employee_eligible=False,
            capabilities=[ToolCapability.SYSTEM],
            handler=speak_handler,
        )
    )

    registry.register(
        ToolDefinition(
            name="voice_status",
            description=(
                "Check the voice output path: how many audio clients are "
                "connected, the effective TTS URL, whether TTS is configured, "
                "and the last playback error. Use this before claiming voice "
                "works or diagnosing missing audio."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=VoiceStatusInput,
            destructive=False,
            employee_eligible=False,
            capabilities=[ToolCapability.SYSTEM],
            handler=voice_status_handler,
        )
    )
