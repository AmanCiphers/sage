# SAGE Implementation Plan with Testing Guidelines

**Created:** 2026-10-25  
**Status:** PLANNING PHASE — READ ONLY  
**Based on:** ARCHITECTURE_PLAN.md + discussion  

---

## Testing Guidelines (MANDATORY)

### For Every Phase/Milestone/Task:
1. **Unit tests** — New code paths, edge cases, error handling (≥80% coverage on new code)
2. **Integration tests** — Subsystem interactions, DB contracts, API boundaries
3. **Regression tests** — Golden master snapshots for compaction, permissions, verification
4. **Contract tests** — Interface compliance (Protocol/ABC implementations)
5. **E2E tests** — Critical user flows (CLI + Web)

### Test Artifacts Required:
- `tests/unit/test_<module>.py` — per new/changed module
- `tests/integration/test_<flow>.py` — per critical flow
- `tests/regression/test_<feature>.py` — per behavioral change
- `tests/contract/test_<interface>.py` — per new interface

### CI Gates:
- `pytest tests/unit -q` — must pass
- `pytest tests/integration -q` — must pass  
- `pytest tests/regression -q` — must pass
- No new warnings/errors

---

## Complete Work Breakdown

### Phase 0 — Baseline & Infrastructure (Week 1)
| Task | Description | Tests Required |
|------|-------------|----------------|
| T0.1 | Enforce Python 3.11+ (`.venv` has 3.12) | CI matrix test |
| T0.2 | Add `transformers` + `tiktoken` as optional extras (`pip install -e ".[tokenizer]"`) | Import test, tokenizer smoke test |
| T0.3 | Feature flag system (`SAGE_FEATURE_*` in `app/config.py`, `app/feature_flags.py`) | Flag toggle tests, default-off behavior |
| T0.4 | Verify test baseline (`pytest tests/unit -q`) | Record pass/fail count, document pre-existing failures |
| T0.5 | DB backup/restore scripts + test restore from `.audit_snapshot/` | Restore test in CI, verify DB functional |

### Phase 1 — Architecture Contracts & Interfaces (Week 2)
| Task | Description | Tests Required |
|------|-------------|----------------|
| T1.1 | Extract interfaces: `IConversationEngine`, `IContextBuilder`, `IToolRegistry`, `IPermissionEngine`, `IWorkerPool`, `IAgentBackend`, `IMemoryStore`, `IScheduler`, `IProactiveEngine`, `IEventBus`, `IInferenceManager`, `IVerification` (new `app/interfaces/` package) | Contract tests: each implementation satisfies Protocol |
| T1.2 | Wire interfaces in `SAGEApplication` (DI) | Integration: app starts, all subsystems wired |
| T1.3 | Feature flags for v2: `REAL_TOKENIZER`, `PARALLEL_TOOLS`, `SKILLS`, `THREAT_SCAN`, `COMPACTION_V2` | Flag gating tests, default-off verified |

### Phase 2 — Database Safeguards & Migration Infra (Week 2–3)
| Task | Description | Tests Required |
|------|-------------|----------------|
| T2.1 | Additive migration framework (`app/persistence/migrations.py`) | Migration idempotency, order, rollback simulation |
| T2.2 | v2 schema: `token_count`, `model_id` on `messages`; `skills` + `skill_versions` tables | Schema test on `.audit_snapshot/sage.db`, query test |
| T2.3 | Backup/restore scripts tested in CI | CI job: backup → corrupt → restore → verify |

### Phase 3 — Core Runtime Improvements (Week 3–5)
| Task | Description | Tests Required |
|------|-------------|----------------|
| **T3.1** | **Real tokenizer** (transformers, Qwen3-Embedding-8B) in `app/context/tokens.py`, `compressor.py`, `builder.py` | Token count matches provider `usage.prompt_tokens` ±10%; fallback to chars/4 |
| **T3.2** | **Compaction v2**: protected head/tail + boundary snapping (never split tool_call↔tool_result) | Never splits pairs; preserves first sys/human/gpt/tool + last 4 turns; golden master regression |
| **T3.3** | **Threat scanning** for context files (audit mode first) in `app/threat_patterns.py`, `builder.py` | Injection patterns blocked; false positive rate <1%; audit logs |
| T3.4 | Persistent event loops for async tools (fix "Event loop closed") | 1000-tool stress test, no "Event loop closed" errors |
| T3.5 | Parallel tool batch execution (independent read-only tools) | Mixed batch: independent parallel, dependent sequential; latency improvement |
| T3.6 | Cancellation token in `ConversationLoop` (mid-stream stop) | Mid-stream cancel → partial response persisted; no leaked tasks |

### Phase 4 — Model & Tool Execution Improvements (Week 4–5)
| Task | Description | Tests Required |
|------|-------------|----------------|
| T4.1 | Toolsets/profiles (coding, research, writing, verification, organization) | Profile gating tests; employee toolset assignment |
| T4.2 | Structured memory tool returns (`status` with sizes; `add`/`remove` return new state) | Memory loop fix: status → add succeeds in 1 call |
| T4.3 | Agent backend streaming events (`AgentEvent`: start/progress/tool/complete/error) | OpenCode/Hermes stream events parse correctly |
| T4.4 | Verification v2: LLM verifier rung for "unverified" tools | Unverified → LLM checks outcome vs objective |

### Phase 5 — Context Management & Memory v2 (Week 5–7)
| Task | Description | Tests Required |
|------|-------------|----------------|
| T5.1 | Memory v2: provenance + evolution + forgetting (supersession, confidence, status) | Fact lifecycle: add → supersede → forget; provenance tracked |
| T5.2 | Skill system v1: `skill_manage` (create/patch/view/list), skill index, `skill_view` | Skill CRUD, invocation, versioning |
| T5.3 | Session search enhancement (skills + memories + conversations) | Ranked results, cross-source relevance |
| T5.4 | RAG v2: reranking + hybrid weight tuning | Configurable weights, cross-encoder optional |

### Phase 6 — Reliability, Security, Observability (Week 6–8)
| Task | Description | Tests Required |
|------|-------------|----------------|
| T6.1 | Structured error classification + retry policies (`FailoverReason`, jittered backoff) | Error class → correct retry behavior; max retries enforced |
| T6.2 | Proactive engine: replace `eval()` with safe AST whitelist evaluator | Only whitelisted AST nodes execute; injection attempts blocked |
| T6.3 | Structured logging + correlation IDs (request_id, task_id, worker_id, operation_id) | Every log line has IDs; traceable end-to-end |
| T6.4 | Metrics: TTFT, latency, tokens, cost, fallback count (Prometheus `/metrics`) | Metrics endpoint exposes all counters; Grafana dashboard template |
| T6.5 | Secret redaction in logs | API keys, tokens, passwords redacted in all output |

### Phase 7 — Integration, Regression, Documentation (Week 8–10)
| Task | Description | Tests Required |
|------|-------------|----------------|
| T7.1 | E2E: coding task via Charlie → OpenCode (Next.js counter, `npm run build` passes) | Full flow test, artifact verification |
| T7.2 | E2E: research task via Alpha → Hermes (cited sources → summary) | Source citation accuracy, completeness |
| T7.3 | Regression: 50-turn conversation with compaction → accurate summary | Golden master comparison |
| T7.4 | Load test: 100 concurrent tool calls (no "Event loop closed", <5% error) | Concurrency stress, resource usage |
| T7.5 | Update all docs: CHANGELOG, HOW_IT_WORKS, ArchitectureV2, DECISIONS | Doc freshness check |

---

## Milestone 1 (First 2 Weeks) — **START HERE**
```
T0.1–T0.5  → Baseline
T1.1–T1.3  → Interfaces + feature flags
T2.1–T2.2  → Migration infra
T3.1       → Real tokenizer (Qwen3-Embedding-8B)
T3.2       → Compaction v2 (boundary snap + protected head/tail)
T3.3       → Threat scanning (audit mode)
```

### Milestone 1 Success Criteria
- [ ] Python 3.11+ enforced and working
- [ ] Feature flags control v2 subsystems
- [ ] Real tokenizer matches provider usage within 10%
- [ ] Compaction never splits tool_call from tool_result
- [ ] Compaction preserves first sys/human/gpt/tool + last 4 turns
- [ ] All existing tests pass (or documented pre-existing failures)
- [ ] `.audit_snapshot/` restore verified

---

## Accepted Decisions (Q1–Q8)

| # | Decision | Rationale |
|---|----------|-----------|
| Q1 | Tokenizer: **Qwen3-Embedding-8B** | Already in SAGE for embeddings, supports Matryoshka |
| Q2 | Compaction threshold: **Keep 50%** | Safer, compaction is fast |
| Q3 | Skill system: **v1 local only** (create/patch/view/list) | Defer org sync/marketplace |
| Q4 | Proactive `eval()`: **AST whitelist** | Simpler, no new deps |
| Q5 | Web SSE cancel: **Cancel current turn only** | Matches user expectation |
| Q6 | Employee toolsets: **Toolset profiles** | More flexible, user-facing |
| Q7 | Memory provenance UI: **CLI + TUI** | Web later |
| Q8 | Python: **Require 3.11+** | `| None` syntax used throughout |

---

## Voice Module — PARALLEL TRACK (Starts After Milestone 2)

**User Decisions:**
- Client target: **All** (Web, TUI, CLI) — single backend
- ASR: **Local** (faster-whisper on machine)
- TTS: **Chatterbox** (home server, HTTP API — docs coming)
- WebRTC signaling: **aiortc** (Python, integrates with FastAPI)
- Audio I/O: **TUI** = PyAudio/PortAudio; **Web** = browser getUserMedia + WebRTC
- Voice activation: **Push-to-talk button first**, then wake word + VAD
- Fallback: **SAGE informs user** if TTS unavailable

---

### Voice Architecture (ArchitectureV2.md §57–59)
```
Mic → WebRTC (Opus, 48kHz) → Silero VAD (30ms frames, noise suppression)
    → Streaming ASR (faster-whisper, token-level)
    → SAGE ConversationLoop (streaming LLM)
    → Token buffer (~10-20 tokens) → Streaming TTS (Chatterbox)
    → WebRTC Opus frames (20ms) → Speaker
```
**Key properties:** Full-duplex, token-level streaming (<100ms first audio), barge-in (VAD during TTS → cancel TTS + LLM + flush ASR), RNNoise/WebRTC NS + AEC3.

---

### Voice Integration Points
| Component | Integration Needed |
|-----------|-------------------|
| `app/voice/` | New package: pipeline, clients, config |
| `ConversationLoop` | `handle_audio()`, `handle_stream_audio()`, `cancel()` for barge-in |
| `InferenceManager` | Already streams tokens — reuse for token-level TTS trigger |
| `EventBus` | Voice events: `vad_start`, `asr_partial`, `asr_final`, `tts_chunk`, `barge_in` |
| TUI (`app/cli_tui.py`) | Push-to-talk key, voice mode indicator, audio level meter |
| Web (`web/src/components/Console.tsx`) | Mic button, WebRTC connection, audio visualization |
| Config | `SAGE_VOICE_ENABLED`, `CHATTERBOX_URL`, `ASR_MODEL`, `VAD_THRESHOLD` |

---

### Phase V — Voice Module (Parallel: Weeks 4–10)
*Starts after Milestone 2 (core runtime stable, feature flags working)*

| Task | Description | Tests Required |
|------|-------------|----------------|
| **V0.1** | **Voice config & feature flag** (`app/config.py`, `app/feature_flags.py`) | Flag gating, default-off, env validation |
| **V0.2** | **Chatterbox HTTP client** (`app/voice/tts/chatterbox.py`) — streaming POST, chunked audio response | Mock server test, chunk parsing, error handling |
| **V0.3** | **faster-whisper ASR client** (`app/voice/asr/faster_whisper.py`) — streaming transcription, VAD integration | Audio fixture test, token-level output, latency |
| **V0.4** | **Silero VAD** (`app/voice/vad/silero.py`) — ONNX model, 30ms frames, barge-in sensitivity mode | Synthetic audio test, speech/non-speech accuracy |
| **V0.5** | **WebRTC transport** (`app/voice/transport/webrtc.py`) — `aiortc`, Opus 48kHz, signaling via FastAPI | Loopback test, ICE negotiation, chunk timing |
| **V0.6** | **Audio I/O abstraction** (`app/voice/audio/`) — PyAudio (TUI), browser MediaStream (Web) | Device enumeration, sample rate conversion |
| **V0.7** | **Voice pipeline orchestrator** (`app/voice/pipeline.py`) — connects VAD→ASR→SAGE→TTS→WebRTC | End-to-end fixture test, barge-in simulation |
| **V0.8** | **ConversationLoop voice integration** — `handle_audio()`, barge-in cancel hook, token buffer for TTS | Barge-in test: TTS playing → speak → cancel → new turn |

### Phase V1 — TUI Voice (Week 5–6)
| Task | Description | Tests Required |
|------|-------------|----------------|
| **V1.1** | **TUI push-to-talk** (`app/cli_tui.py`) — key binding (Space), voice mode indicator, audio level meter | Key press test, UI state, meter animation |
| **V1.2** | **TUI audio capture** — PyAudio stream, 48kHz, 20ms chunks, VAD gating | Device test, chunk timing, VAD gating |
| **V1.3** | **TUI playback** — PyAudio output, Chatterbox Opus chunks, barge-in stop | Playback test, interrupt test |

### Phase V2 — Web Voice (Week 6–7)
| Task | Description | Tests Required |
|------|-------------|----------------|
| **V2.1** | **WebRTC signaling** (`web/backend.py`) — `/api/voice/connect`, `/api/voice/offer`, `/api/voice/ice` | Signaling test, ICE candidate exchange |
| **V2.2** | **Web Console voice UI** (`web/src/components/Console.tsx`) — mic button, connection status, audio viz | Component test, WebRTC connection, media permissions |
| **V2.3** | **Browser audio** — `getUserMedia`, Opus encoding via Web Audio API, Chatterbox playback | MediaStream test, codec negotiation |

### Phase V3 — Advanced Features (Week 8–9)
| Task | Description | Tests Required |
|------|-------------|----------------|
| **V3.1** | **Barge-in** — VAD runs during TTS → detect speech → cancel TTS (WebRTC track stop) → abort LLM → flush ASR buffer → new turn | Barge-in simulation: TTS playing → inject speech → verify cancel + new ASR |
| **V3.2** | **Wake word** — Silero VAD + lightweight keyword spotter (optional: Porcupine) | Wake word detection accuracy, false positive rate |
| **V3.3** | **Noise suppression + AEC3** — RNNoise/WebRTC NS in VAD front-end, AEC3 for full-duplex | Noise injection test, echo cancellation test |

### Phase V4 — Polish & Fallback (Week 9–10)
| Task | Description | Tests Required |
|------|-------------|----------------|
| **V4.1** | **Fallback handling** — Chatterbox unavailable → SAGE informs user (no silent failure) | Network error test, user-facing message |
| **V4.2** | **Voice config persistence** — per-user settings, device selection | Settings save/load, device migration |
| **V4.4** | **Documentation & E2E test** — `tests/e2e/test_voice_pipeline.py` | Full pipeline: speak → ASR → SAGE → TTS → hear response |

---

### Voice Test File Additions
```
tests/
├── unit/
│   ├── test_chatterbox_client.py
│   ├── test_faster_whisper_asr.py
│   ├── test_silero_vad.py
│   ├── test_webrtc_transport.py
│   ├── test_voice_pipeline.py
│   └── test_barge_in.py
├── integration/
│   ├── test_voice_tui.py
│   ├── test_voice_web.py
│   └── test_voice_barge_in.py
├── e2e/
│   └── test_voice_pipeline.py
```

---

### Voice Dependencies (add to pyproject.toml)
```toml
[project.optional-dependencies]
voice = [
    "aiortc>=1.9",
    "faster-whisper>=1.0",
    "onnxruntime>=1.16",      # Silero VAD
    "pyaudio>=0.2.13",        # TUI audio I/O
    "numpy>=1.24",            # Audio processing
    "opuslib>=3.0",           # Opus encoding/decoding
]
```

---

### Voice Milestones
| Milestone | Target | Gate |
|-----------|--------|------|
| **V-M1** | TUI push-to-talk works (speak → hear response) | After V0.1–V0.8 + V1.1–V1.3 |
| **V-M2** | Web voice works (browser mic → hear response) | After V2.1–V2.3 |
| **V-M3** | Barge-in works (interrupt TTS mid-stream) | After V3.1 |
| **V-M4** | Production ready (fallback, wake word, noise suppression) | After V3.2–V4.1 |

**Gate Rule:** Voice track gates only proceed after corresponding main track milestones (e.g., V-M1 after Main Milestone 2).

---

## Quick Reference: Test File Naming Convention

```
tests/
├── unit/
│   ├── test_tokens_real_tokenizer.py
│   ├── test_compaction_boundaries.py
│   ├── test_threat_scanner.py
│   ├── test_memory_tool_status.py
│   ├── test_toolset_profiles.py
│   ├── test_skill_manage.py
│   ├── test_ast_evaluator.py
│   └── test_metrics.py
├── integration/
│   ├── test_conversation_loop.py
│   ├── test_employee_workflow.py
│   ├── test_agent_backend_streaming.py
│   └── test_proactive_engine.py
├── regression/
│   ├── test_compaction_golden_master.py
│   ├── test_permissions_golden_master.py
│   └── test_verification_golden_master.py
├── contract/
│   ├── test_interfaces.py
│   └── test_feature_flags.py
└── e2e/
    ├── test_coding_charlie_opencode.py
    ├── test_research_alpha_hermes.py
    └── test_voice_pipeline.py   # FUTURE
```

---

## Execution Rules

1. **One task at a time** — complete T0.1 before T0.2, etc.
2. **Tests first** — write failing test, then implementation (TDD where practical)
3. **Commit per task** — each task = atomic commit with tests
4. **Gate reviews** — Milestone 1 → Gate 1 review before Milestone 2
5. **Voice track** starts after Main Milestone 2 (Gate 2), gated by corresponding main milestones

---

**Next Action:** Await go-ahead to begin Milestone 1 implementation.