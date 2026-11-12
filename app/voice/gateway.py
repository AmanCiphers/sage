"""WebSocket gateway for the voice module.

Protocol:
- text frames (JSON)  : ``mode``, ``ptt_start``, ``ptt_end``, ``cancel``
- binary frames       : 16-bit mono PCM audio frames (16 kHz by default)

Server->client:
- text frames (JSON)  : ``state``, ``transcript``, ``response``, ``error``

The voice socket is transcription-only. The browser opens the normal
streaming chat endpoint after receiving ``transcript``; SAGE's written/streamed
reply and tool activity stay on that path.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.voice.asr import make_asr
from app.voice.config import get_voice_config
from app.voice.session import VoiceEvents, VoiceSession
from app.voice.vad import make_vad
from app.voice.wakeword import make_wakeword


def build_session(websocket: WebSocket) -> VoiceSession:
    cfg = get_voice_config()
    vad = make_vad(cfg)
    asr = make_asr(cfg)
    wake = make_wakeword(cfg)

    def emit(kind: str, payload: Any = None) -> None:
        msg = {"type": kind}
        if payload is not None:
            msg["payload"] = payload

        async def _send() -> None:
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps(msg))

        _background_tasks.add(asyncio.create_task(_send()))

    _background_tasks: set = set()

    events = VoiceEvents(
        state=lambda s: emit("state", s),
        transcript=lambda t: emit("transcript", t),
        response=lambda r: emit("response", r),
        error=lambda e: emit("error", e),
    )

    return VoiceSession(
        vad=vad,
        asr=asr,
        wake=wake,
        turn_handler=None,
        cfg=cfg,
        events=events,
    )


_voice_test_tasks: set[asyncio.Task] = set()


async def _voice_test(sage_app: Any, websocket: WebSocket) -> None:
    heard = await sage_app.speak_text(
        "Voice test. One, two, three.", confirm=True
    )
    with contextlib.suppress(Exception):
        await websocket.send_text(
            json.dumps({"type": "voice_test", "payload": {"heard": bool(heard)}})
        )


async def voice_endpoint(websocket: WebSocket) -> None:
    from app.application import get_sage_app
    from app.feature_flags import voice_enabled

    sage_app = get_sage_app()
    if sage_app is None:
        await websocket.accept()
        await websocket.send_text(
            json.dumps({"type": "error", "payload": "SAGE not initialized"})
        )
        await websocket.close()
        return

    if not voice_enabled():
        await websocket.accept()
        await websocket.send_text(
            json.dumps({"type": "error", "payload": "Voice not enabled"})
        )
        await websocket.close()
        return

    await websocket.accept()
    session = build_session(websocket)
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] != "websocket.receive":
                continue
            if msg.get("bytes"):
                session.feed(msg["bytes"])
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                kind = data.get("type")
                if kind == "mode":
                    session.set_mode(data.get("payload", "ptt"))
                elif kind == "ptt_start":
                    session.ptt_start()
                elif kind == "ptt_end":
                    session.ptt_end()
                elif kind == "cancel":
                    session.cancel()
                elif kind == "test_voice":
                    task = asyncio.create_task(_voice_test(sage_app, websocket))
                    _voice_test_tasks.add(task)
                    task.add_done_callback(_voice_test_tasks.discard)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        session.cancel()
