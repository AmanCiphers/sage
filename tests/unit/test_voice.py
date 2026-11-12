"""Unit tests for the voice module using fake engines (no live models)."""

import asyncio
import io
import json
import types
import wave

import httpx
import pytest

from app.tools.registry import ToolRegistry
from app.tools.speak_tool import register_speak_tools, reset_speak_flag
from app.voice.audio import pcm_to_wav
from app.voice.audio_bus import AudioBus
from app.voice.config import VoiceConfig
from app.voice.session import VoiceEvents, VoiceSession
from app.voice.tts import ChatterboxTTS, chunk_text
from app.voice.vad import EnergyVAD

FRAME = b"\x00\x00" * 160  # ~10ms of 16k mono silence (320 bytes)


def make_config(**overrides) -> VoiceConfig:
    base = {
        "sample_rate": 16000,
        "frame_ms": 30,
        "silence_ms": 700,
        "max_turn_seconds": 60.0,
        "asr_model": "base.en",
        "asr_device": "cpu",
        "asr_compute_type": "int8",
        "vad_threshold": 0.02,
        "use_silero": False,
        "wake_enabled": False,
        "wake_keyword": "hey_jarvis",
        "chatterbox_url": "http://localhost:9/tts",
        "tts_primary": "chatterbox",
        "tts_fallback": "piper",
        "piper_voice": "en_US-lessac-medium",
        "model_dir": __import__("pathlib").Path("/tmp/sage-voice-test"),
    }
    base.update(overrides)
    return VoiceConfig(**base)


class _Collector:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.transcripts: list[str] = []
        self.responses: list[str] = []
        self.errors: list[str] = []


class FakeVAD:
    def is_speech(self, frame: bytes) -> bool:
        return False

    def reset(self) -> None:
        pass


class FakeASR:
    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe(self, audio: bytes, sample_rate: int) -> str:
        return self._text

    async def close(self) -> None:
        pass


class FakeWake:
    def __init__(self, detect_after: int = -1) -> None:
        self._count = 0
        self._detect_after = detect_after

    def feed(self, frame: bytes) -> bool:
        self._count += 1
        return self._count > self._detect_after

    def reset(self) -> None:
        pass


async def _async_return(value):
    return value


async def test_ptt_turn_flows_end_to_end():
    col = _Collector()
    session = VoiceSession(
        vad=FakeVAD(),
        asr=FakeASR("hello sage"),
        wake=FakeWake(),
        turn_handler=lambda text: _async_return("Full written reply here. Goodbye."),
        cfg=make_config(),
        events=VoiceEvents(
            state=col.states.append,
            transcript=col.transcripts.append,
            response=col.responses.append,
            error=col.errors.append,
        ),
    )

    session.ptt_start()
    for _ in range(20):
        session.feed(FRAME)
    session.ptt_end()
    await session._task

    assert col.transcripts == ["hello sage"]
    assert col.responses == ["Full written reply here. Goodbye."]
    assert col.errors == []
    assert col.states[0] == "listening"
    assert col.states[-1] == "idle"


async def test_transcription_only_session_skips_turn():
    col = _Collector()
    session = VoiceSession(
        vad=FakeVAD(),
        asr=FakeASR("hello sage"),
        wake=FakeWake(),
        turn_handler=None,
        cfg=make_config(),
        events=VoiceEvents(
            state=col.states.append,
            transcript=col.transcripts.append,
            response=col.responses.append,
            error=col.errors.append,
        ),
    )

    session.ptt_start()
    for _ in range(20):
        session.feed(FRAME)
    session.ptt_end()
    await session._task

    assert col.transcripts == ["hello sage"]
    assert col.responses == []
    assert col.errors == []
    assert col.states[-1] == "idle"


async def test_empty_audio_does_not_call_turn_handler():
    col = _Collector()
    calls = []

    async def turn(text: str) -> str:
        calls.append(text)
        return "x"

    session = VoiceSession(
        vad=FakeVAD(),
        asr=FakeASR(""),
        wake=FakeWake(),
        turn_handler=turn,
        cfg=make_config(),
        events=VoiceEvents(
            state=col.states.append,
            transcript=col.transcripts.append,
            response=col.responses.append,
            error=col.errors.append,
        ),
    )

    session.ptt_start()
    session.ptt_end()
    await session._task

    assert calls == []
    assert col.transcripts == []


async def test_wake_word_mode_triggers_turn():
    col = _Collector()

    async def turn(text: str) -> str:
        return "Replied."

    session = VoiceSession(
        vad=FakeVAD(),
        asr=FakeASR("woken"),
        wake=FakeWake(detect_after=2),
        turn_handler=turn,
        cfg=make_config(),
        events=VoiceEvents(
            state=col.states.append,
            transcript=col.transcripts.append,
            response=col.responses.append,
            error=col.errors.append,
        ),
    )

    session.set_mode("wake")
    # 3 frames: 3rd triggers the wake word -> starts a turn.
    for _ in range(3):
        session.feed(FRAME)
    # Continue feeding audio after the trigger so a turn is captured.
    for _ in range(10):
        session.feed(FRAME)
    session.ptt_end()
    await session._task

    assert col.transcripts == ["woken"]
    assert col.responses == ["Replied."]


def test_energy_vad_detects_speech():
    vad = EnergyVAD(threshold=0.02)
    loud = b"\x7f\x7f" * 160
    assert vad.is_speech(loud) is True
    assert vad.is_speech(FRAME) is False


@pytest.mark.asyncio
async def test_voice_session_cancel_resets():
    col = _Collector()
    session = VoiceSession(
        vad=FakeVAD(),
        asr=FakeASR("x"),
        wake=FakeWake(),
        turn_handler=lambda t: _async_return("x"),
        cfg=make_config(),
        events=VoiceEvents(
            state=col.states.append,
            transcript=col.transcripts.append,
            response=col.responses.append,
            error=col.errors.append,
        ),
    )
    session.ptt_start()
    session.cancel()
    assert session._ptt is False
    assert col.states[-1] == "idle"


def _wav(frames: int = 160, rate: int = 24000) -> bytes:
    return pcm_to_wav(b"\x01\x00" * frames, rate)


def _tts_with_handler(handler, **kwargs) -> ChatterboxTTS:
    return ChatterboxTTS(
        "http://tts.test/tts",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_chunk_text_is_sentence_aware():
    chunks = chunk_text("First sentence. Second sentence. Third sentence.", 30)
    assert chunks
    assert all(len(c) <= 30 for c in chunks)
    assert " ".join(chunks) == "First sentence. Second sentence. Third sentence."


def test_chunk_text_handles_long_sentence():
    chunks = chunk_text("word " * 50, 40)
    assert chunks
    assert all(len(c) <= 40 for c in chunks)
    assert all(c.strip() for c in chunks)


async def test_synthesize_chunks_streams_in_order():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["text"]
        seen.append(text)
        return httpx.Response(200, content=_wav(), headers={"content-type": "audio/wav"})

    text = "First sentence here. Second sentence here. Third sentence here."
    expected = chunk_text(text, 25)
    tts = _tts_with_handler(handler, chunk_chars=25, prefetch=2)
    try:
        parts = [part async for part in tts.synthesize_chunks(text)]
    finally:
        await tts.close()

    assert len(parts) == len(expected)
    assert all(part[:4] == b"RIFF" for part in parts)
    assert seen == expected


async def test_synthesize_merges_chunks_into_one_wav():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_wav(frames=160), headers={"content-type": "audio/wav"})

    text = "First sentence here. Second sentence here. Third sentence here."
    expected = chunk_text(text, 25)
    tts = _tts_with_handler(handler, chunk_chars=25, prefetch=1)
    try:
        merged = await tts.synthesize(text)
    finally:
        await tts.close()

    with wave.open(io.BytesIO(merged), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 24000
        assert w.getnframes() == 160 * len(expected)


async def test_synthesize_rejects_non_wav_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not a wav")

    tts = _tts_with_handler(handler)
    try:
        with pytest.raises(ValueError):
            await tts.synthesize("Hello.")
    finally:
        await tts.close()


class _FakeSink:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.binaries: list[bytes] = []

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.binaries.append(data)


class _FakeTTS:
    url = "http://tts.test/tts"

    async def synthesize_chunks(self, text: str):
        yield pcm_to_wav(b"\x01\x00" * 160, 24000)


class _FakeTaskStore:
    def __init__(self, tasks: list[dict]) -> None:
        self._tasks = tasks

    async def list_for_worker(self, worker: str, limit: int = 20):
        return self._tasks[:limit]


async def test_audio_bus_reports_zero_without_clients():
    bus = AudioBus()
    result = await bus.broadcast(_wav(), expect_ack=True)
    assert result.delivered == 0
    assert result.heard is False
    assert "No audio client" in (bus.status()["last_error"] or "")


async def test_audio_bus_waits_for_playback_ack():
    bus = AudioBus()
    sink = _FakeSink()
    bus.subscribe(sink)

    task = asyncio.create_task(bus.broadcast(_wav(), expect_ack=True, timeout=1.0))
    while not sink.texts:
        await asyncio.sleep(0)
    audio_id = json.loads(sink.texts[0])["id"]
    bus.confirm(audio_id, True)
    result = await task

    assert result.delivered == 1
    assert result.heard is True
    assert sink.binaries == [_wav()]


async def test_audio_bus_ack_timeout_is_not_heard():
    bus = AudioBus()
    sink = _FakeSink()
    bus.subscribe(sink)
    result = await bus.broadcast(_wav(), expect_ack=True, timeout=0.01)
    assert result.delivered == 1
    assert result.heard is False
    assert "not confirmed" in (bus.status()["last_error"] or "")


async def test_speak_reports_heard_after_ack():
    reset_speak_flag()
    bus = AudioBus()
    sink = _FakeSink()
    bus.subscribe(sink)
    registry = ToolRegistry()
    register_speak_tools(registry, lambda: _FakeTTS(), bus)

    task = asyncio.create_task(registry.get("speak").handler(text="hello"))
    while not sink.texts:
        await asyncio.sleep(0)
    audio_id = json.loads(sink.texts[0])["id"]
    bus.confirm(audio_id, True)
    result = await task

    assert result["spoken"] is True
    assert result["heard"] is True
    assert result["delivered"] == 1


async def test_speak_without_audio_client_is_not_success():
    reset_speak_flag()
    bus = AudioBus()
    registry = ToolRegistry()
    register_speak_tools(registry, lambda: _FakeTTS(), bus)

    result = await registry.get("speak").handler(text="hello")
    assert result["spoken"] is False
    assert "No audio client" in result["error"]


def test_voice_status_tool_reports_connection_and_url():
    bus = AudioBus()
    registry = ToolRegistry()
    register_speak_tools(registry, lambda: _FakeTTS(), bus)
    status = registry.get("voice_status").handler()
    assert status["tts_configured"] is True
    assert status["tts_url"] == "http://tts.test/tts"
    assert status["audio_clients"] == 0


async def test_supervisor_find_duplicate(monkeypatch):
    from app.employees import supervisor as supervisor_module
    from app.employees.supervisor import Supervisor

    monkeypatch.setattr(
        supervisor_module,
        "get_spec",
        lambda worker: types.SimpleNamespace(key=worker),
    )
    tasks = [
        {
            "task_id": "task_1",
            "objective": "Write an essay",
            "status": "running",
            "error": None,
            "created_at": "now",
        }
    ]
    sup = Supervisor.__new__(Supervisor)
    sup.task_store = _FakeTaskStore(tasks)

    active = await sup.find_duplicate("otto", "write an  ESSAY")
    assert active is not None
    assert active["reason"] == "active"

    tasks[0]["status"] = "failed"
    failed = await sup.find_duplicate("otto", "Write an essay")
    assert failed is not None
    assert failed["reason"] == "failed"

    missing = await sup.find_duplicate("otto", "something else")
    assert missing is None


async def test_synthesize_without_client_chunking_uses_one_request():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["text"])
        return httpx.Response(200, content=_wav(), headers={"content-type": "audio/wav"})

    text = "First sentence here. Second sentence here."
    tts = _tts_with_handler(handler, client_chunking=False)
    try:
        parts = [part async for part in tts.synthesize_chunks(text)]
    finally:
        await tts.close()

    assert len(parts) == 1
    assert seen == [text]
