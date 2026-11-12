# Voice Module

> **What this document contains:** How SAGE's voice path works today —
> enabling it, push-to-talk vs. wake word, the browser↔backend pipeline, the
> `app/voice/` modules, the `speak` tool that produces audio, configuration,
> the `/ws/voice` protocol, resource notes for Apple Silicon, and what is still
> deferred. It covers the **built push-to-talk slice**, not the full-duplex
> WebRTC target in `ArchitectureV2.md` §57–59.

SAGE voice: **push-to-talk / wake word → STT → SAGE → `speak` tool → TTS → speaker**.

Voice is another client of SAGE, not a separate intelligence — a voice turn is a
normal `UserMessage` through the same conversation engine.

## Enable

```bash
# .env
SAGE_FEATURE_VOICE=1
```

Restart the backend (`./start-web.sh`). When enabled, the Web UI shows a
`voice` bar above the composer (the `/api/info` `voice` field gates it).

## Interaction model

- **Push-to-talk (default, resource-light):** hold the `hold` button, speak,
  release. The turn is transcribed; the transcript is inserted into the chat
  and SAGE's reply streams there like a typed turn. SAGE calls the `speak`
  tool for what should be spoken.
- **Wake word (opt-in):** toggle `wake:on`; the mic runs continuously and a
  keyword (default `hey_jarvis`) starts a turn, auto-finalized after trailing
  silence. Off by default to keep idle CPU low.

Each voice turn carries `voice: true` through the normal streaming chat path.
`ConversationLoop` injects a per-turn system instruction requiring SAGE to call
`speak` at least once (the legacy `app/decision/personality.py` mandate is not
part of the live conversation prompt); the tool is limited to one call per turn
so a response cannot stack audio. If the model still omits `speak`, the backend
synthesizes the final text as a fallback so every voice turn is audible.

## Pipeline

```
Mic (16 kHz PCM) ──/ws/voice──> VAD ─> faster-whisper ─> transcript
                                                              │
                                    browser POST /api/chat/stream
                                          { message, voice: true }
                                                              │
                                  SAGE turn (streaming SSE) ──┼──> chat UI
                                                              │
                              SAGE calls `speak(text)` ◄──────┘
                                                              ▼
                                      TTS ─> WAV ─> AudioBus ─> /ws/audio
                                                              │
                                                      browser speaker
```

- The `/ws/voice` socket is **transcription-only**: it emits the transcript and
  the browser starts the turn on the normal streaming endpoint. Thinking,
  tool, delta, approval, and fallback events are therefore identical to typed
  chat. Streaming happens over SSE, not over the voice socket.
- **Full written response** streams into the chat. Audio is produced only when
  SAGE calls the **`speak` tool** (`app/tools/speak_tool.py`), which synthesizes
  the given text and broadcasts the WAV through `AudioBus` to `/ws/audio`.
- **Chunked TTS:** long text is split into sentence-aware chunks
  (`SAGE_VOICE_TTS_CHUNK_CHARS`, default 220), requested with bounded prefetch,
  and broadcast one WAV per chunk as it becomes ready. The browser plays them
  through a FIFO queue, so speech starts after the first chunk and chunks never
  overlap. The server itself chunks internally (120 chars, 60 ms crossfade);
  set `SAGE_VOICE_TTS_CLIENT_CHUNKING=false` to send the whole text and wait for
  its single merged WAV instead.
- **Playback confirmation:** before each WAV the server sends
  `{"type":"audio","id":"<uuid>","bytes":N}` on `/ws/audio`. The browser replies
  `{"type":"audio_played","id":...}` once playback starts, or
  `{"type":"audio_failed","id":...,"error":...}` if decoding/autoplay fails.
  `speak` returns `heard: true` only after that acknowledgement. With no
  connected audio client it returns `spoken: false`; if playback is not
  confirmed it returns `heard: false` with a warning. The `voice_status` tool
  and `GET /api/voice/status` expose the connected-client count, effective TTS
  URL, and last playback error.
- SAGE decides what to say aloud; there is no automatic sentence-extraction
  step in `VoiceSession` (`app/voice/session.py` only transcribes when the
  gateway supplies no turn handler).
- `speak` may be called **once per turn**; a second call returns an error. If
  it is not called at all on a voice turn, `SAGEApplication.speak_text` speaks
  the final text as a guarantee.

## Backend layout (`app/voice/`)

| File | Purpose |
|---|---|
| `config.py` | `VoiceConfig` from settings (`SAGE_VOICE_*`), frame size, model paths |
| `interfaces.py` | `VAD` / `ASR` / `TTS` / `WakeWord` Protocols |
| `audio.py` | PCM<->float and WAV helpers |
| `vad.py` | energy VAD (default, zero download) or Silero ONNX if present |
| `asr.py` | faster-whisper (CTranslate2), lazy-loaded, runs off the event loop |
| `tts.py` | Chatterbox (local server) primary, Piper fallback |
| `wakeword.py` | openWakeWord (opt-in) |
| `audio_bus.py` | `AudioBus`: fan-out TTS WAV to `/ws/audio`, delivery counts, playback ACK |
| `../tools/speak_tool.py` | `speak` (chunked broadcast + `heard`) and `voice_status` tools |
| `session.py` | `VoiceSession`: trigger → ASR → transcript (optional turn handler; web passes none) |
| `gateway.py` | `/ws/voice` WebSocket handler (transcription transport) |
| `server_test/` | local helper for the TTS server |

TTS output is produced by the **`speak` tool** (`app/tools/speak_tool.py`),
not by `session.py`. The tool is registered in `app/application.py` and uses
`app/voice/tts.py` plus `AudioBus`.

## Resource-light (Mac Air M4)

- No torch. `onnxruntime` (CPU) + faster-whisper `base.en` int8.
- Models load **lazily** and only during an active turn; ASR runs only after
  speech is captured. Idle = no inference.
- Chatterbox TTS is a network call (near-zero local compute); Piper is the
  offline fallback only.

## Config (`SAGE_VOICE_*`)

| Setting | Default | Meaning |
|---|---|---|
| `SAGE_VOICE_ASR_MODEL` | `base.en` | faster-whisper model |
| `SAGE_VOICE_ASR_DEVICE` | `cpu` | device |
| `SAGE_VOICE_ASR_COMPUTE_TYPE` | `int8` | quantization |
| `SAGE_VOICE_SAMPLE_RATE` | `16000` | Hz |
| `SAGE_VOICE_FRAME_MS` | `30` | frame length |
| `SAGE_VOICE_SILENCE_MS` | `700` | trailing silence to finalize (wake) |
| `SAGE_VOICE_MAX_TURN_SECONDS` | `60` | maximum audio buffered per turn |
| `SAGE_VOICE_VAD_THRESHOLD` | `0.02` | energy VAD RMS threshold |
| `SAGE_VOICE_USE_SILERO` | `false` | use Silero if model file present |
| `SAGE_VOICE_WAKE_ENABLED` | `false` | enable wake word |
| `SAGE_VOICE_WAKE_KEYWORD` | `hey_jarvis` | keyword |
| `SAGE_VOICE_CHATTERBOX_URL` | `https://tts.thecloverforge.com/tts` | TTS server |
| `SAGE_VOICE_TTS_PRIMARY` | `chatterbox` | primary TTS |
| `SAGE_VOICE_TTS_FALLBACK` | `piper` | fallback TTS |
| `SAGE_VOICE_PIPER_VOICE` | `en_US-lessac-medium` | Piper voice |
| `SAGE_VOICE_TTS_CLIENT_CHUNKING` | `true` | split text client-side for lower latency |
| `SAGE_VOICE_TTS_CHUNK_CHARS` | `220` | maximum characters per TTS request |
| `SAGE_VOICE_TTS_PREFETCH` | `2` | chunks fetched ahead of playback |
| `SAGE_VOICE_TTS_TIMEOUT_SECONDS` | `30` | per-request TTS timeout |
| `SAGE_VOICE_SPOKEN_LLM` | `true` | **Legacy / unwired.** Defined in `Settings` but not used by `VoiceConfig` or the voice pipeline; spoken output comes from the `speak` tool. |
| `SAGE_VOICE_MODEL_DIR` | `data/voice` | model files |

## WebSocket protocol (`/ws/voice`)

Client → server:
- text: `{"type":"mode","payload":"ptt"|"wake"}`, `ptt_start`, `ptt_end`,
  `cancel`, `test_voice`
- binary: 16-bit mono PCM frames

Server → client:
- text: `state` / `transcript` / `error`
- The optional `response` event is still part of `VoiceEvents` for non-web
  turn handlers, but the web gateway passes `turn_handler=None` and never
  emits it.
- Audio is **not** sent on `/ws/voice`; TTS WAV frames arrive on `/ws/audio`.
- `/ws/audio` is bidirectional: the server sends a JSON metadata frame then the
  WAV; the client sends `audio_played` / `audio_failed` acknowledgements. The
  `test_voice` control triggers a spoken test line and replies with
  `{"type":"voice_test","payload":{"heard":true|false}}`.

## Deferred

- Full-duplex WebRTC / AEC3 echo cancellation (current design is push-to-talk,
  which avoids the echo problem).
- Always-on wake streaming + custom "Sage" wake-word model.
- Multi-session concurrency.