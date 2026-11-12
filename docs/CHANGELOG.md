# SAGE Change Log

> **What this document contains:** A running, chronological record of changes to
> SAGE's architecture, behavior, tools, prompts, and data model. Part A lists
> landed changes newest-first; Part B keeps the employee-worker design record;
> Part C is a hardening backlog. Each entry records date, area, change, files,
> and why. This is a **historical record**, annotated rather than rewritten.
>
> - **Design rationale (older, formalized decisions):** [DECISIONS.md](DECISIONS.md)
> - **How the running system works:** [HOW_IT_WORKS.md](HOW_IT_WORKS.md)
> - **Full target architecture:** [ArchitectureV2.md](ArchitectureV2.md)

This file exists because not every change made it into `DECISIONS.md`. Anything
that changes *how SAGE behaves, what it trusts, or how its parts talk to each
other* gets an entry here — even small ones. Pure cosmetics get a one-liner.

---

## Conventions

Each entry:

- **Date** — when the change landed.
- **Area** — Web UI / Permissions / Events / Workers / Tooling / etc.
- **Change** — what changed.
- **Files** — where.
- **Why** — the reason (this is the important part).

Forward-looking designs (not yet built) live in a clearly marked **Proposed**
section so they are never confused with shipped behavior.

---

# Part A — Recently Made Changes

Changes from the web-UI integration work that were **not** recorded in
`DECISIONS.md`.

## A49 — Hardline `rm` rule matched anywhere, not only at command position  *(2026-11-12)*

- **Area:** Permissions
- **Change:** `_RM_FLAG_PREFIX` in `app/permissions/approval.py` no longer
  anchors to `_CMDPOS`. A recursive delete of a protected root is now
  recognised when `rm` is path-prefixed (`/bin/rm -rf /`), chained
  (`echo x; rm -rf /`), or nested in a shell (`bash -c 'rm -rf /'`). The
  optional path prefix plus a word boundary keeps `charm` and `firm` from
  matching, and relative targets (`rm -rf ./build`, `node_modules`) are
  unaffected.
- **Files:** `app/permissions/approval.py`, `tests/unit/test_permission_engine.py`
- **Why:** Those five forms degraded from `deny` (never bypassable) to `ask`,
  i.e. a prompt a rushed user clicks through. The hardline rule is the one
  layer every other bypass sits behind, so a hole in it is worse than a hole
  anywhere else. See D49.

## A48 — Engineering audit: undefined names, subprocess deadlocks, capability routing  *(2026-11-12)*

- **Area:** Tools / Agents / Permissions / Persistence / Skills / Interfaces
- **Change:**
  - `skill_view(version=...)` no longer raises `NameError` — the filter now
    reads `[v for v in versions if ...]`.
  - Agent backends no longer deadlock or crash on subprocess output. Hermes
    drained stderr *after* stdout to completion, so a child that filled the
    stderr pipe blocked forever; the non-zero-exit warning also referenced an
    undefined `error` name. Both backends now drain stderr concurrently and
    read lines through the shared `iter_output_lines()` helper, which chunks
    the stream so a single line above asyncio's 64 KiB reader limit no longer
    raises `LimitOverrunError`. OpenCode's streaming path had the same
    stdout-then-stderr pattern.
  - `_resolve_run_command_capabilities` matches network/system commands on
    the **basename**, so `/usr/bin/curl` and `./curl` classify as NETWORK
    instead of falling through to EXECUTE. Command sets hoisted to
    `NETWORK_COMMANDS` / `SYSTEM_COMMANDS` (39 duplicate literals removed).
  - Removed shadowed duplicate definitions: `_check`/`_parse` in
    `verification.py`, `_has_destructive_patterns`/`_has_metacharacters` in
    `shell_parser.py`, `list_skills`/`get_skill_versions` in
    `skills/store.py`, and the function-local `run_all_migrations` /
    `settings` re-imports.
  - `app/interfaces/__init__.py` imports `PermissionDecision`, so
    `get_type_hints()` on `IPermissionEngine` resolves instead of raising
    `NameError`.
- **Files:** `app/skills/tools.py`, `app/agents/interface.py`,
  `app/agents/hermes.py`, `app/agents/opencode.py`, `app/tools/shell_parser.py`,
  `app/execution/verification.py`, `app/skills/store.py`,
  `app/persistence/schema.py`, `app/interfaces/__init__.py`,
  `tests/unit/test_agent_backends.py`, `tests/unit/test_skill_tools.py`,
  `tests/unit/test_shell_capabilities.py`, `tests/unit/test_permission_engine.py`
- **Why:** These were confirmed defects, not style issues. The `NameError`
  paths broke versioned skill reads and every failed Hermes run. The
  stdout-before-stderr drain is a latent hang in production (OpenCode can take
  20–30 min; a hung child means a hung assignment). The path-prefix gap let a
  command change capability class by being invoked as `./curl`. The
  duplicates were dead code that looked live.

## A47 — Secret-file reads detected by argument, not by verb list  *(2026-11-12)*

- **Area:** Permissions
- **Change:** The credential rule in `classify_command` matched only
  `cat|cp|mv|rsync`. Replaced with `_touches_secret_file()`: the command is
  tokenized with `shlex`, `$HOME` expanded, and every argument's path
  components checked against the secret-file/directory sets. Any reader is
  caught (`less`, `head`, `grep`, `base64`, `xxd`, `strings`, `tar czf ~/.ssh`).
  Tunnel-vision exclusions keep it quiet where it should be: listing/navigation
  commands (`ls`, `cd`, `find`, `tree`) and template files
  (`.env.example`, `.sample`, `.template`, `.dist`).
- **Files:** `app/permissions/risk.py`, `tests/unit/test_permission_engine.py`
- **Why:** Adversarial probing showed `less ~/.ssh/id_rsa`, `grep SECRET .env`,
  `base64 ~/.ssh/id_rsa` and `tar czf x.tgz ~/.ssh` were all **allowed**, while
  only the four listed verbs asked. With a prompt-injectable model as the
  caller, any reader is a realistic credential-exfiltration route. See D48.

## A46 — Regression tests for previously untested edges  *(2026-11-12)*

- **Area:** Tests
- **Change:** Added `tests/unit/test_agent_backends.py` (real subprocess stubs:
  stderr-pipe overflow and overlong-line), `tests/unit/test_skill_tools.py`
  (versioned skill read), `tests/unit/test_shell_capabilities.py`
  (path-prefix routing), plus parametrized secret-path cases in
  `test_permission_engine.py`. Each was confirmed to fail against the buggy
  code and pass against the fix.
- **Files:** new tests above; `tests/unit/test_permission_engine.py`
- **Why:** The fake-provider suite covers internal contracts only. Agent
  backends had **zero** tests, which is why two crash paths and a deadlock
  survived. These tests exercise the real subprocess and permission edges.

## A45 — Live date/time injected into the system prompt every turn  *(2026-11-11)*

- **Area:** Context / Conversation / Employees / Tools
- **Change:** `ContextBuilder._system_content()` now appends a
  `## Current date and time` section (weekday, date, time, UTC offset, and the
  configured IANA timezone) to the system message on every `build()` call.
  Both the main agent and employee workers use `ContextBuilder`, so all of them
  get a fresh clock each turn. The section is present even when no memory store
  is configured. `get_current_time` / `get_current_date` now read the same
  `settings.timezone` instead of the host OS timezone, so tool answers match the
  injected clock.
- **Files:** `app/context/builder.py`, `app/tools/builtin.py`,
  `tests/unit/test_context_builder.py`, `docs/HOW_IT_WORKS.md`
- **Why:** SAGE had no passive sense of "now" — the static system prompt was
  composed once at startup, and the time/date tools only answer when the model
  chooses to call them. Injecting a live clock makes date/time awareness
  automatic and correct for relative requests ("today", "tomorrow", "this
  afternoon") while the tools remain for explicit queries.

---

## A45 — Voice playback confirmation + truthful speak result  *(2026-11-11)*

- **Area:** Voice / Tools / Web UI / Employees
- **Change:** `/ws/audio` now carries a JSON metadata frame (`{"type":"audio",
  "id":...}`) before each WAV; the browser replies `audio_played` or
  `audio_failed`. `AudioBus` tracks connected sinks, delivery counts, and
  waits for the acknowledgement, so `speak` returns `heard`, `delivered`, and
  `chunks` instead of a blanket `spoken: true`. With no audio client it returns
  `spoken: false`; unconfirmed playback carries a warning. Added the
  `voice_status` tool and `GET /api/voice/status` (client count, effective TTS
  URL, last playback error). `VoiceBar` reconnects both sockets with backoff,
  shows audio connection state, and has a **test** button. Prompt rules now
  scope work to the latest user message and forbid claiming playback without
  `heard: true`. `assign_task` reuses an active duplicate and returns
  `needs_confirmation` for a recently failed match instead of silently retrying
  (`force=true` after the user agrees).
- **Files:** `app/voice/audio_bus.py`, `app/voice/gateway.py`,
  `app/tools/speak_tool.py`, `app/tools/builtin.py`,
  `app/employees/supervisor.py`, `app/application.py`, `web/backend.py`,
  `web/src/components/VoiceBar.tsx`, `app/conversation/authority.md`,
  `app/conversation/tools.md`, `tests/unit/test_voice.py`
- **Why:** `AudioBus.broadcast()` silently no-ops with zero subscribers and the
  old `speak` tool still reported success, so SAGE claimed voice worked while
  the browser received nothing (typically after a backend restart left a stale
  audio socket). Separately, an older unresolved essay request resurfaced on a
  later "try it" turn and was re-queued without asking. Playback now has to be
  confirmed, and background work is scoped to the current request.

---

## A44 — Chunked TTS: pipelined synthesis + sequential playback  *(2026-11-11)*

- **Area:** Voice / Tools / Web UI
- **Change:** The TTS client now splits long text into sentence-aware chunks
  (`SAGE_VOICE_TTS_CHUNK_CHARS`, default 220), requests them with bounded
  prefetch, and broadcasts each WAV to `/ws/audio` as soon as it is ready.
  `speak` and the final-text fallback stream chunks instead of waiting for one
  large response. The browser now plays incoming WAV frames through a FIFO
  queue (one `AudioBufferSourceNode` at a time), so multiple chunks never
  overlap. The default Chatterbox URL moved to
  `https://tts.thecloverforge.com/tts`.
- **Files:** `app/voice/tts.py`, `app/tools/speak_tool.py`,
  `app/application.py`, `app/config.py`, `app/voice/config.py`,
  `web/src/components/VoiceBar.tsx`, `tests/unit/test_voice.py`, `.env.example`
- **Why:** The TTS server now chunks internally (120-char pieces, crossfaded)
  and returns one merged WAV; waiting for the whole result delayed speech
  start. Client-side chunking lets the first sentence play while the rest
  synthesizes. The sequential queue prevents garbled, overlapping audio when
  several WAV frames arrive in quick succession.

---

## A43 — Stop an in-flight SAGE turn  *(2026-11-11)*

- **Area:** Web UI / Backend / Conversation
- **Change:** Added `POST /api/chat/stop`, which cancels the active streaming
  turn. The composer's send button becomes a red **STOP** while SAGE is
  working; it aborts the browser fetch and cancels the backend task. The SSE
  stream ends with `{"stopped": true}` + `{"done": true}`.
- **Files:** `web/backend.py`, `web/src/components/Console.tsx`
- **Why:** Users need to terminate a long or runaway "thinking" turn without
  waiting for the full response or killing the process.

---

## A42 — Web UI drops CORS; chat goes through same-origin proxy  *(2026-11-11)*

- **Area:** Web UI / Backend
- **Change:** Removed `CORSMiddleware` from the FastAPI backend. The frontend
  chat stream now posts to the relative `/api/chat/stream`, served by the
  Next.js rewrite to `:8000`, so requests are same-origin and need no CORS.
- **Files:** `web/backend.py`, `web/src/components/Console.tsx`
- **Why:** The Next.js dev server auto-shifts to another port when 3000 is
  occupied (e.g. 3003). Chat previously hit `:8000` directly, so a mismatched
  origin failed the CORS preflight with `400 Bad Request`. Routing through the
  existing rewrite removes the cross-origin request entirely.

---

## A41 — Primary chat model moved to DeepSeek-V4.1-Flash-1017  *(2026-10-30)*

- **Area:** Inference / Config
- **Change:** The Nebius primary catalog entry now uses
  `deepseek-ai/DeepSeek-V4.1-Flash-1017` (same priority 90, tool calling and
  streaming enabled, reasoning off). The NVIDIA `openai/gpt-oss-20b` fallback,
  OpenCode default model, and smart-guardian model are unchanged.
- **Files:** `config/models.py`, `README.md`
- **Why:** Requested upgrade of SAGE's primary chat model to the newer V4.1
  Flash build.

---

## A40 — Voice turns stream through the chat pipeline  *(2026-10-30)*

- **Area:** Voice / Web UI / Conversation Loop
- **Change:**
  - `/ws/voice` is now a **transcription-only** transport: it emits `state` and
    `transcript` and passes `turn_handler=None` to `VoiceSession`.
  - The browser inserts the transcript into the chat and starts a normal
    `POST /api/chat/stream` turn with `voice: true`.
  - `ConversationLoop.handle`/`handle_stream` accept `voice` and inject a
    per-turn system instruction requiring the `speak` tool.
  - `SAGEApplication.speak_text` + `speak_was_called` provide a fallback: if a
    voice turn never calls `speak`, the final text is synthesized and
    broadcast, so every voice response is audible.
  - Voice mode survives approval continuation (`voice` query param on the
    approve/deny endpoints).
  - `VoiceBar` reports the transcript to `Console` instead of only rendering
    SAGE's final response; the chat now shows the user's spoken line, the
    thinking indicator, and streamed deltas/tool events.
- **Files:** `app/voice/session.py`, `app/voice/gateway.py`,
  `app/conversation/loop.py`, `app/tools/speak_tool.py`,
  `app/application.py`, `app/interfaces/__init__.py`, `web/backend.py`,
  `web/src/components/VoiceBar.tsx`, `web/src/components/Console.tsx`
- **Why:** The voice gateway ran a non-streaming turn in parallel with the
  chat, so the chat showed only the final reply (no user transcript, no
  thinking spinner, no live deltas) and the model never received a signal that
  it was a voice turn. Routing voice through the existing SSE path removes the
  duplicate path and makes voice behave exactly like typed chat, with a
  guaranteed spoken response.

---

## A39 — Documentation reconciled with the current codebase  *(2026-10-30)*

- **Area:** Docs
- **Change:**
  - Added a "What this document contains" header to every file in `docs/`.
  - Corrected living docs to match the working tree: `HOW_IT_WORKS.md`
    (config names/defaults, LLM verifier, full tool list, memory v2, hybrid
    retrieval, CLI/TUI, module map), `voice.md` (the `speak`-tool pipeline and
    real file layout), and `RAG_IMPLEMENTATION.md` (reranker, session search).
  - Annotated historical docs without rewriting them:
    `WORKER_EMPLOYEE_PLAN.md` marked implemented; `ArchitectureV2.md` status
    banners for memory (§30–33) and voice (§57–59); `DECISIONS.md` notes for
    D5 (tokenizer), D7 (streaming), and D14 (hybrid RAG).
  - Fixed broken `../ArchitectureV2.md` links (the file lives in `docs/`).
- **Files:** `docs/CHANGELOG.md`, `docs/DECISIONS.md`, `docs/HOW_IT_WORKS.md`,
  `docs/ArchitectureV2.md`, `docs/RAG_IMPLEMENTATION.md`,
  `docs/WORKER_EMPLOYEE_PLAN.md`, `docs/voice.md`
- **Why:** Several docs had drifted from the code (wrong defaults, a described
  voice design that no longer exists, memory features marked unbuilt despite
  being implemented behind flags, and incomplete tool/module inventories). This
  pass makes the docs trustworthy again while preserving the historical record.

## A38 — Voice fixes: stale-server port conflict, VoiceBar reconnect churn, gateway send errors  *(2026-10-29)*

- **Area:** Voice / Web UI
- **Change:**
  - `VoiceBar` kept its `onResponse` callback in a ref so the `/ws/voice`
    connection is stable (the inline arrow was re-running the connect effect
    on every render, causing connect/disconnect churn and log spam).
  - The voice gateway now wraps `send_text`/`send_bytes` in try/except so a
    client that disconnects mid-turn no longer raises
    `ClientDisconnected`/`WebSocketDisconnect` tracebacks.
- **Files:** `web/src/components/VoiceBar.tsx`, `app/voice/gateway.py`
- **Why:** After the voice module landed, a stale backend was still bound to
  `:8000`, so a fresh `start-web.sh` hit `Address already in use` and the UI
  talked to the old code. Clean restart + stable connection + graceful send
  handling make the running voice slice reliable.

## A37 — Voice module: push-to-talk / wake word → STT → SAGE → conversational line → TTS  *(2026-10-29)*

- **Area:** Voice / Web UI / Config
- **Change:**
  - Added `app/voice/` package: `config`, `interfaces` (VAD/ASR/TTS/WakeWord
    Protocols), `audio` (PCM/WAV helpers), `vad` (energy default, Silero
    optional), `asr` (faster-whisper, lazy + off-loop), `tts` (Chatterbox
    primary, Piper fallback), `wakeword` (openWakeWord, opt-in), `spoken`
    (conversational line, option a), `session` (VoiceSession), `gateway`
    (`/ws/voice` WebSocket).
  - New `/ws/voice` endpoint (gated by `SAGE_FEATURE_VOICE=1`) and a `voice`
    field on `/api/info` to gate the UI.
  - `VoiceBar` component in the Web UI: mic capture (AudioContext →
    16 kHz PCM16), push-to-talk hold + optional wake-word mode, WAV playback,
    live transcript + spoken line; full response is pushed into the chat.
  - Voice config block added to `Settings` (`SAGE_VOICE_*`) with
    resource-light defaults for Apple-Silicon/CPU: no torch, lazy-loaded
    models, `base.en` int8, push-to-talk default.
  - `docs/voice.md` added; ArchitectureV2 §57-59 marked partially built.
- **Files:** `app/voice/*`, `app/config.py`, `web/backend.py`,
  `web/src/components/VoiceBar.tsx`, `web/src/components/Console.tsx`,
  `tests/unit/test_voice.py`, `docs/voice.md`
- **Why:** The voice architecture was designed (§57-59) but unimplemented
  (empty `app/voice/`, deps declared but not installed). This delivers a
  working vertical slice (talk to SAGE) with a wake-word/PTT trigger and a
  concise spoken line, while keeping CPU/RAM low on the M4 (no torch, lazy
  models, Chatterbox TTS as a network call).

## A36 — Show the approved command + auto-continue after approval  *(2026-10-29)*

- **Area:** Permissions / Web UI / Conversation
- **Change:**
  - Approval prompts now surface the **actual command**: the loop's display
    resource is the normalized `command` for `run_command` (not `path`),
    and the Web UI builds a `Run command: <cmd>` description/target. The
    duplicated `write_file`/`patch`/`run_opencode` branches in
    `web/backend.py` were collapsed and a bare `except` tightened.
  - After the user approves an action, SAGE **continues the task** instead of
    just confirming: `approve_pending` now tells the model to proceed to the
    next step and stop at the next approval. The Web approve endpoint returns
    any follow-up `pending_approval`, and `Console.tsx` re-shows the approval
    banner automatically — so a multi-step job flows one approval at a time
    without the user re-prompting.
- **Files:** `app/conversation/loop.py`, `app/application.py`,
  `web/backend.py`, `web/src/components/Console.tsx`,
  `tests/unit/test_permission_engine.py`
- **Why:** A `run_command` approval showed an empty target, so users had to
  approve commands they couldn't see; and after approving, the turn ended
  with a bare "Approved." confirmation, forcing the user to manually say
  "continue" (and the model often dropped earlier steps, e.g. a `mkdir`).

## A35 — Risk-based permissions, working smart guardian, session approval memory  *(2026-10-29)*

- **Area:** Permissions / Tools / Web UI / Config
- **Change:**
  - New deterministic risk classifier (`app/permissions/risk.py`) maps every
    invocation to `allow` / `review` / `ask` / `deny`. Ordinary reads, normal
    shell commands, and ordinary file edits run silently; known-dangerous
    commands ask; ambiguous shell constructs (`; | & $() xargs eval bash -c`,
    heredocs, redirects) go to the smart guardian; catastrophic commands are
    hard-denied and cannot be overridden by YOLO or approval.
  - The engine (`app/permissions/engine.py`) now runs `run_command` and file
    writes through that classifier before any bypass mode, reads approvals
    from the allowlist, and consults the smart guardian asynchronously
    (`check_async`) only for `review` results. It no longer references the
    nonexistent `auto_allow_read` / `session_allow_write` /
    `explicit_destructive` config attributes that raised `AttributeError`
    whenever YOLO was off.
  - Removed the stateless `requires_approval` hooks from `write_file`,
    `patch`, and `run_command` (the loop's single `check_async` enforcement
    point replaces them). `authorize_workspace` is now a registered tool.
  - Approval memory is scoped: the approve endpoint accepts
    `scope=session|permanent` (session by default). A session approval is
    keyed by tool + normalized resource (`run_command:<cmd>`,
    `write_file:<abs path>`), so repeating the same command no longer
    re-prompts; different commands still do.
  - `full_machine_access` (default `true`) grants SAGE access to the whole
    Mac; sensitive files outside an explicitly authorized directory still
    ask. Pointing SAGE at a directory (`authorize_workspace`, persistent)
    makes relative paths resolve there and silences prompts for files inside
    it. `is_path_authorized` honors the flag.
  - Fixed the hardline `rm -rf` pattern so `/tmp/x` asks instead of being
    hard-denied, while `/`, `/*`, `/home/...`, `/etc`, etc. remain
    unbypassable denies.
  - Fixed `SmartGuardian.assess` to return `GuardianAssessment` (with
    lowercase verdicts) instead of a tuple, and removed the call to the
    undefined `_run_smart_guardian_fallback`.
  - Fixed the broken approval-panel JSX and missing `tool` field in the
    Console `Approval` type so `next build` type-checks.
- **Files:** `app/permissions/risk.py` (new), `app/permissions/engine.py`,
  `app/permissions/smart.py`, `app/permissions/approval.py`,
  `app/tools/file_tools.py`, `app/tools/shell_tools.py`,
  `app/tools/workspace.py`, `app/config.py`, `app/conversation/loop.py`,
  `app/application.py`, `web/backend.py`, `web/src/components/Console.tsx`,
  `tests/unit/test_permission_engine.py`, `tests/unit/test_approvals_and_operations.py`
- **Why:** Permission handling was both noisy and fragile. Web approvals were
  one-shot (the allowlist was never updated), so the same read/write prompted
  forever; with YOLO disabled the engine crashed on missing config
  attributes; the smart guardian was called through a missing function and
  returned the wrong type; and the hardline blocklist was dead code. The
  user wants ordinary work to feel silent and safe, dangerous actions to ask
  once, and catastrophic actions to be impossible.

## A1 — Web SSE stream bypasses the Next.js dev proxy  *(2026-10-20)*

- **Area:** Web UI / Transport
- **Change:** The chat SSE stream is fetched directly from
  `http://localhost:8000/api/chat/stream` instead of the proxied `/api/*`
  path. A `SSE_BASE` constant was introduced in the console component.
- **Files:** `web/src/components/Console.tsx`
- **Why:** The Next.js dev proxy buffers the response body until the stream
  completes **when the browser sends `Accept-Encoding: gzip`** (which all
  browsers do). Verified with `curl --compressed` through port 3000: the whole
  stream flushed at once at the end. Hitting the backend directly (CORS already
  allows `http://localhost:3000`) restores true live streaming — thinking,
  tool calls, and deltas appear as they happen. Same pattern the WebSocket
  already used. **Note:** the production build hardcodes `localhost:8000`; an
  env var (e.g. `NEXT_PUBLIC_SSE_BASE`) is needed before deploying.

## A2 — SSE now carries LoopEvents  *(2026-10-20)*

- **Area:** Web UI / Events
- **Change:** `POST /api/chat/stream` now forwards conversation `LoopEvent`s —
  `llm_start` → `{"stage":"thinking"}`, `tool_start` → `{"stage":"tool",...}`,
  `tool_end` → `{"tool":...,"ok":...}`, `compaction` → `{"notice":...}`, and
  `approval` → `{"approval":{...}}` — interleaved with text `delta` frames,
  followed by `done`.
- **Files:** `web/backend.py` (`chat_stream`, `on_loop_event`)
- **Why:** Previously the SSE stream only emitted deltas, an approval, and a
  done marker, so the UI had no "thinking" or tool-call lines. The loop already
  emits these events (D21); the web layer just wasn't consuming them. LoopEvents
  were deliberately **removed from the WebSocket** earlier (to stop duplicate
  log lines) and now travel exclusively over SSE.

## A3 — Raw system-event forwarding + clean summaries  *(2026-10-20)*

- **Area:** Web UI / Events
- **Change:** `ConnectionManager._forward_system_event` now broadcasts a
  uniform shape: `{type:"event", source, event_type, summary, data}` where
  `data` is the raw `Event.to_dict()` and `summary` is a short human one-liner.
  A `_summarize(event)` helper builds those lines (e.g.
  `app/core/foo.py was written`, `job fired · jlpt_prep_reminder`,
  `task completed · task_ab12`).
- **Files:** `web/backend.py`
- **Why:** The old code collapsed nearly every event to a generic
  `{"event":"sys","data":{…}}`, which leaked raw JSON into the chat feed and
  gave panels nothing to filter on. Forwarding the raw event plus a clean
  summary lets each panel filter by `source` (`worker`/`scheduler`/`proactive`/
  `task`) and display short lines instead of full payloads — per the UI rule
  "never show a full log."

## A4 — New REST endpoints for panels  *(2026-10-20)*

- **Area:** Web UI / API
- **Change:** Added `GET /api/tasks` (recent background tasks) and
  `GET /api/scheduler/jobs` (scheduled jobs). Added `TaskStore.list_recent()`
  to back the tasks endpoint.
- **Files:** `web/backend.py`, `app/persistence/store.py`
- **Why:** Panels use a **hybrid** model: WebSocket for live events, REST for
  initial/backfill state. The scheduler-rules endpoint already existed; jobs and
  tasks did not. `list_recent` deliberately omits the (potentially huge)
  `result` blob — the UI shows summaries, not full outputs.

## A5 — Permission relaxations  *(2026-10-20)*

- **Area:** Permissions
- **Change:**
  - File edits (`write_file`, `patch`) **never** require approval —
    `_write_needs_approval` now always returns `False` (previously editing an
    existing file *outside* the workspace required consent).
  - `remove_job` and `remove_rule` were made **non-destructive**
    (`destructive=False`) and their `requires_approval` hooks now return
    `False` (previously `destructive=True` + always-approve).
  - Scheduler and proactive metadata tools
    (`list_jobs`, `schedule_job`, `list_rules`, `add_proactive_rule`,
    `enable_job`, `enable_rule`) were already approval-free.
- **Files:** `app/tools/file_tools.py`, `app/tools/builtin.py`
- **Why:** User request — editing files, and viewing/removing scheduled jobs
  and proactive tasks, should not nag for approval. Approval was being enforced
  in two places (the engine's `destructive` check **and** the dynamic
  `requires_approval` hook); both had to be cleared.

## A6 — Tool schema/handler fix for rules  *(2026-10-20)*

- **Area:** Tooling
- **Change:** `remove_rule` and `enable_rule` reused `RemoveJobInput` /
  `EnableJobInput`, whose field is `job_id`, while their wired handlers expect
  `rule_id` — so the LLM (reading the schema) called `remove_rule(job_id=…)`
  and hit `unexpected keyword argument 'job_id'`. Added dedicated
  `RemoveRuleInput` (`rule_id`) and `EnableRuleInput` (`rule_id`, `enabled`)
  schemas; `enable_rule`'s handler now honors the `enabled` argument instead of
  hardcoding `True`.
- **Files:** `app/tools/builtin.py`, `app/application.py`
- **Why:** The schema the model sees, the handler signature, and the wiring
  must all agree. They didn't for rules.

## A7 — Approval id normalization  *(2026-10-20)*

- **Area:** Web UI / Approvals
- **Change:** The SSE `done`-path approval frame now includes an `id` key
  (mirrored from the DB's `approval_id`). The frontend `Approval` type accepts
  either `id` or `approval_id`, resolves whichever is present, and guards
  against a missing id. A `lastApprovalId` ref de-dupes the repeated
  "approval required" line.
- **Files:** `web/backend.py`, `web/src/components/Console.tsx`
- **Why:** Approval was emitted twice — once from the `LoopEvent` (with `id`)
  and once from the final response using the raw DB dict keyed by
  `approval_id`. The second overwrote the first, so `pending.id` became
  `undefined` and the UI POSTed to `/api/approvals/undefined/approve`, yielding
  `Unknown approval: undefined`.

## A8 — UI fixes and worker panel  *(2026-10-20)*

- **Area:** Web UI
- **Change:**
  - **Chat scroll:** the page is now `h-screen overflow-hidden` and every flex
    ancestor in the scroll chain got `min-h-0`, so the chat tape scrolls
    internally instead of growing the page.
  - **Live thinking indicator:** a `ThinkingLine` component with a braille
    spinner + pulsing "sage is thinking" text + blinking caret, shown between
    send and the first delta / done / error / approval.
  - **Autoscroll:** added the missing effect that sets
    `scrollTop = scrollHeight` on new lines (the state and ref existed but were
    never wired up).
  - **Worker panel:** added alongside the `session` card — REST-polled task list
    (`/api/tasks`, every 4 s) with status pills (queued/running/completed/
    failed) plus a live "activity" feed from `sysEvents`.
  - **`sysEvents` buffer:** system events are collected in state and routed by
    source; the chat feed no longer shows them.
- **Files:** `web/src/components/Console.tsx`
- **Why:** Direct bug reports (no scroll, no autoscroll, no thinking feedback)
  plus the first of the planned side panels (worker), consistent with the
  "chat first, then worker/logs/scheduler" rollout order.

## A9 — Dependencies  *(2026-10-20)*

- **Area:** Tooling
- **Change:** Added `fastapi`, `uvicorn`, `websockets` to `pyproject.toml`.
- **Files:** `pyproject.toml`
- **Why:** Required by the FastAPI web backend (`web/backend.py`).

## A10 — Employee completion notifications on Web UI  *(2026-10-22)*

- **Area:** Web UI / Events
- **Change:** `Console.tsx` now polls `/api/notices` every 3s when idle and handles `employee_finished`/`task_failed` WebSocket events, pushing completion notices to the main chat feed.
- **Files:** `web/src/components/Console.tsx`, `web/backend.py` (existing `/api/notices` endpoint)
- **Why:** Previously, employee completion only updated the Worker panel via WebSocket and the CLI; the main web chat never showed "Asta finished task_123" unless you manually checked the panel. Now SAGE announces completions in-chat automatically.

## A11 — Agent backends: OpenCode & Hermes integration  *(2026-10-22)*

- **Area:** Employees / Tooling
- **Change:** Added `run_opencode` and `run_hermes` tools (employee_eligible=True, requires approval). Employees can now invoke external agent CLIs for coding and general tasks. Session continuity via `--continue`/`--session` (OpenCode) and `--continue`/`--resume` (Hermes). Added `cancel_task` (SAGE-only) to terminate runaway tasks.
- **Files:** `app/agents/interface.py`, `app/agents/opencode.py`, `app/agents/hermes.py`, `app/agents/factory.py`, `app/agents/sessions.py`, `app/tools/builtin.py`, `app/employees/roles/*.md`, `app/config.py`, `app/persistence/schema.py`
- **Why:** Employees previously only had local tools (read, write, shell). Real coding tasks need a proper agent. OpenCode excels at multi-file edits; Hermes at research. Session tracking in `agent_sessions` table enables resume.

## A12 — Unlimited employee budgets + SAGE cancellation  *(2026-10-22)*

- **Area:** Employees / Permissions
- **Change:** `employee_max_wall_seconds` default changed from 600s → 0 (unlimited). Wall-clock budget removed as runaway guard; SAGE now uses `cancel_task` to intervene when an employee runs too long (OpenCode can take 20-30 min on complex tasks).
- **Files:** `app/config.py`, `app/tools/builtin.py` (`cancel_task`), `app/employees/supervisor.py` (`cancel_task`)
- **Why:** Hard timeouts were killing legitimate long-running coding tasks. SAGE-as-manager should decide when to intervene, not a timer.

## A13 — Permission fixes: read-only commands + employee agent tools  *(2026-10-24)*

- **Area:** Permissions / Employees / Tooling
- **Change:**
  - `run_command` with read-only commands (allowlist + no metacharacters: `ls`, `cat`, `grep`, `git status`, etc.) → no approval required
  - `run_opencode` / `run_hermes` when invoked by an employee → no approval required (budgeted, verified, cancellable)
  - Same tools when invoked directly by SAGE → still require approval
  - Agent tool classification changed: `EXTERNAL_SIDE_EFFECT` → `LOCAL_MUTATION`
  - Fixed hardcoded employee DB paths: `run_opencode` now uses calling employee's session DB (was always `charlie`); `run_hermes` uses calling employee's DB (was always `alpha`)
  - Added `current_employee` context var (`app/tools/workspace.py`) set by Supervisor when spawning employee task
  - Updated Charlie's role card: explicit instruction to use `run_opencode` for coding tasks
- **Files:** `app/permissions/engine.py`, `app/tools/builtin.py`, `app/tools/workspace.py`, `app/employees/supervisor.py`, `app/employees/roles/charlie.md`
- **Why:** Approval per agent call made autonomous employee work impossible ("approval storm"). Employees already have hard budgets, verification, and SAGE's `cancel_task` kill switch. Read-only commands were incorrectly gated by `EXTERNAL_SIDE_EFFECT` check. Hardcoded DB paths broke session continuity for non-Charlie/Alpha employees.

## A14 — Employee roster aliases + role display  *(2026-10-24)*

- **Area:** Employees / CLI
- **Change:**
  - Added `role` field to `EmployeeSpec` with one-line descriptions (Coder, Researcher, Writer, Verifier, Organizer)
  - Added `_ALIASES` mapping: "coder" → "charlie", "researcher" → "alpha", "writer" → "otto", "verifier" → "tony", "organizer" → "asta"
  - `normalize()` now accepts role aliases (e.g., `assign_task(worker="coder", ...)` works)
  - `roster_status()` API now includes `role` field for UI display
  - Added `roster_summary()` helper for simple display
- **Files:** `app/employees/roster.py`, `app/employees/supervisor.py`
- **Why:** User said "coder employee" but SAGE didn't recognize it. Now "coder", "engineer", "developer" all resolve to Charlie. Roster display shows correct roles instead of hallucinated descriptions.

## A15 — Voice architecture: full-duplex WebRTC + token-level streaming  *(2026-10-24)*

- **Area:** Voice / Architecture
- **Change:**
  - Pipeline: WebRTC (Opus) → Silero VAD (streaming, noise suppression) → Streaming ASR → SAGE → Streaming LLM → Token-level streaming TTS → WebRTC
  - Full-duplex: simultaneous send/receive, no half-duplex turn-taking
  - Barge-in: Silero VAD runs during TTS; detection → immediate TTS cancel + LLM abort + ASR flush
  - Token-level TTS: first audio at ~80-120ms after first LLM token (no sentence-boundary wait)
  - Noise suppression + AEC3 echo cancellation integrated
- **Files:** `docs/ArchitectureV2.md` (sections 57-59)
- **Why:** Previous architecture was half-duplex with sentence-level streaming. Full-duplex WebRTC + token-level TTS enables natural conversation with mid-speech interruption.

## A16 — Employee tool usage guide + agent tool integration  *(2026-10-24)*

- **Area:** Employees / Tooling
- **Change:**
  - Updated `tools.md` with full documentation for `run_opencode` and `run_hermes` (when to use, permission model, session continuity)
  - Fixed role mapping in `tools.md`: Charlie → "Coder / Engineer" (was "organizing/operations")
  - Employees now receive `tools.md` content in their system prompt (appended to role card) via `app/employees/factory.py`
  - Added integration tests verifying Charlie calls `run_opencode` for coding tasks, Alpha calls `run_hermes` for research
- **Files:** `app/conversation/tools.md`, `app/employees/factory.py`, `tests/unit/test_employees.py`
- **Why:** Employees had role cards saying "use run_opencode" but no tool usage guide in their system prompt. They couldn't make informed tool choices. Now they get the same tool guidance as SAGE.

## A17 — Per-employee log files + tool usage visibility  *(2026-10-24)*

- **Area:** Employees / Observability
- **Change:**
  - Each employee now has its own log file at `data/employees/<key>/sage.log` with DEBUG-level logging
  - Added detailed logging in `Employee.work()`: LLM iterations, tool starts/ends with arguments, completion summary
  - `run_opencode` and `run_hermes` now log "SAGE is using OpenCode/Hermes via employee X" or "SAGE is using OpenCode/Hermes directly" for visibility
  - Fixed FTS5 search syntax errors with improved escaping and fallback query
- **Files:** `app/employees/factory.py`, `app/employees/employee.py`, `app/tools/builtin.py`, `app/persistence/store.py`
- **Why:** No visibility into what employees were actually doing. "Completed" status with empty result was misleading. Per-employee logs + tool usage logs make debugging and verification straightforward.

## A18 — Employee actually uses OpenCode (reasoning model + timeout)  *(2026-10-24)*

- **Area:** Employees / Inference
- **Change:**
  - Employees now route to a **reasoning-capable model** (`openai/gpt-oss-20b`) instead of DeepSeek-V4-Flash, so they follow complex system-prompt instructions (e.g. "call run_opencode") rather than defaulting to `run_command`.
  - `ConversationLoop` accepts a `model_requirements` param; employee factory passes `ModelRequirements(tool_calling=True, reasoning=True)`.
  - Provider timeout raised `60s → 180s` (reasoning model is slower); this also fixed the intermittent gpt-oss-20b timeouts.
  - Rewrote `run_opencode`/`run_hermes`/`run_command` tool descriptions to be explicit about when each is mandatory, and that `run_command`/`write_file`/`patch` are NOT for coding.
- **Files:** `app/employees/factory.py`, `app/conversation/loop.py`, `app/config.py`, `app/tools/builtin.py`, `app/tools/shell_tools.py`, `app/employees/roles/charlie.md`
- **Why:** Employees returned hollow "completed" because the fast model ignored the run_opencode instruction. Verified live: Charlie called `run_opencode`, produced a real Next.js counter app, and the production build passed.

## A19 — Runtime full-permission toggle (/yolo) + project-root path resolution  *(2026-10-24)*

- **Area:** Permissions / Tooling / CLI
- **Change:**
  - Added runtime `set_yolo_mode()` in `app/permissions/config.py`; CLI `/yolo` enables full permission (no approvals), `/approvals` re-enables them.
  - `resolve_path()` in `app/tools/file_tools.py` now falls back to the project root for relative paths (e.g. `data/employees/...`) that don't exist under the workspace, so SAGE can inspect employee stores without absolute paths.
  - Fixed approval-expiry error that occurred when a slow `run_opencode` created an approval that expired before subsequent verification commands were approved.
- **Files:** `app/permissions/config.py`, `main.py`, `app/tools/file_tools.py`, `tests/unit/test_employees.py`
- **Why:** "Full permission" was not actually honored — SAGE kept prompting for approvals. And `data/employees/...` paths resolved under `data/workspace` (not found) instead of the project root. Both fixed.

## A20 — Hermes wrapper fix + YOLO-by-default + unlimited SAGE budget  *(2026-10-24)*

- **Area:** Agents / Permissions / Config
- **Change:**
  - **Hermes wrapper fixed** (`app/agents/hermes.py`): the old command used `hermes chat --format stream-json`, which doesn't exist in Hermes v0.20.x and failed instantly. Now uses the top-level one-shot form `hermes -z <prompt> --in <dir> --pass-session-id`, letting Hermes use its own configured model/provider (NVIDIA NIM) instead of hardcoded `--provider openrouter --model openai/gpt-4`.
  - **YOLO-by-default but destructive still gated**: `.env` now sets `SAGE_YOLO_MODE=1`. `PermissionEngine._check_bypass()` was updated so YOLO allows everything **except** genuinely destructive tools (still `APPROVAL_REQUIRED`). The `run_command` dynamic `requires_approval` hook still gates non-read-only commands.
  - **Unlimited SAGE budget**: SAGE's own loop `max_iterations` raised `8 → 50` (per-turn tool rounds). Employee budgets were already unlimited (all `0`).
  - **Timeout fix stuck**: `.env` had `SAGE_MODEL_REQUEST_TIMEOUT=60`, silently overriding the earlier `180s` default and re-breaking gpt-oss-20b. Updated `.env` to `180`.
- **Files:** `app/agents/hermes.py`, `app/permissions/engine.py`, `.env`, `app/config.py`, `tests/unit/test_permission_engine.py`
- **Why:** `run_hermes` was broken (invalid CLI flags) and SAGE kept prompting for approvals despite full-permission intent. Now Hermes works end-to-end, approvals only gate destructive actions, and SAGE won't run out of budget or hit the stale 60s timeout.

## A21 — Investigation & web tools, await_task (anti-poll), per-role employee allowlists  *(2026-10-24)*

- **Area:** Tools / Employees / Loop / Web
- **Change:**
  - **Investigation tools** (new `app/tools/investigation.py`, ripgrep-backed):
    - `grep` — search file contents (regex/literal, `path`, `include` glob, case, limit, timeout).
    - `glob` — find files by glob pattern (`**/*.ts`), hidden/limit.
    - `web_search` — ranked results; uses **Tavily** if `SAGE_TAVILY_API_KEY` set, else free **Bing RSS** (no CAPTCHA).
    - `browse_url` — fetch a page and return its text (HTML→text).
  - **`await_task` tool + `Supervisor.await_task()`** — wait asynchronously for a task to finish (or timeout) instead of polling. **Anti-poll circuit-breaker** in `ConversationLoop`: after 2 repeated `task_status` calls on the same running task, injects guidance to use `await_task` / end turn.
  - **`list_files`** upgraded: `recursive` + `limit`. **`read_file`** now prefixes 1-based line numbers.
  - **Per-role employee tool allowlists** (enforced in `factory._filter_registry`): Charlie gets coding tools; Alpha/Asta research/survey tools; Otto write tools; Tony read/verify tools. No more uniform toolset.
  - **Employees get `session_search` + a working `memory` tool** (per-employee `memory.md` resolved via context; was orphaned before).
  - **Prompt quality**: role cards gained a "Tools you have" section; `tools.md` gained investigation heuristics + "don't poll task_status" rule; `authority.md` gained the employee roster + polling guidance.
- **Files:** `app/tools/investigation.py` (new), `app/tools/builtin.py`, `app/tools/file_tools.py`, `app/tools/memory_tools.py`, `app/tools/session_search.py`, `app/employees/factory.py`, `app/employees/supervisor.py`, `app/conversation/loop.py`, `app/conversation/tools.md`, `app/conversation/authority.md`, `app/employees/roles/*.md`, `app/config.py`, `app/application.py`, `app/inference/fake.py`, tests
- **Why:** SAGE had no grep/glob/web/browse tools (OpenCode had them all), employees were given a uniform toolset (not role-fit), and SAGE burned ~1177k tokens in a `task_status` poll runaway. These changes fix all three: investigation capability, role-fit tooling, and the poll loop.

## A22 — Docs-maintenance mandate + memory-architecture doc sync  *(2026-10-24)*

- **Area:** Docs / Tooling
- **Change:**
  - Added **`AGENTS.md`** at the repo root instructing every agent that works on this repository to update `docs/CHANGELOG.md` (and `docs/DECISIONS.md` for architectural decisions) as part of finishing any change.
  - Recorded D37–D40 in `docs/DECISIONS.md` (await_task/anti-poll, investigation & web tools, per-role allowlists, docs mandate).
  - **Memory-architecture reality sync** (from a source-vs-docs audit): `docs/ArchitectureV2.md` §30 (Current State), §31 (provenance), §32 (evolution), §33 (forgetting) are **planned, not built**; and `docs/DECISIONS.md` D14's "no embeddings" is **stale** — hybrid RAG (FTS5 + embeddings) is live. (Full memory upgrade deferred to a later session.)
- **Files:** `AGENTS.md` (new), `docs/CHANGELOG.md`, `docs/DECISIONS.md`, `docs/ArchitectureV2.md`
- **Why:** Several design docs diverged from the running system (D14 said no embeddings; §30–33 memory features are unimplemented). A persistent AGENTS.md reminder prevents future staleness and keeps the decision log authoritative.

## A23 — Fix grep/glob asyncio conflict  *(2026-10-24)*

- **Area:** Tools
- **Change:** Made `grep` and `glob` (and their shared `_run` helper) **async** so they await the ripgrep subprocess directly inside SAGE's running event loop. Previously they used `asyncio.run()`, which crashed with *"asyncio.run() cannot be called from a running event loop"* whenever SAGE invoked them.
- **Files:** `app/tools/investigation.py`, `tests/unit/test_investigation_and_polling.py`
- **Why:** `web_search`/`browse_url` worked because they use sync stdlib HTTP, but the ripgrep subprocess helpers tried to spin their own loop. Making them async aligns with the `ExecutionEngine` (which awaits async handlers) and fixes the crash.

## A24 — Timezone-aware scheduler + one-time `run_at` reminders  *(2026-10-24)*

- **Area:** Scheduler / Cron / Proactive
- **Change:**
  - **Timezone fix**: `SchedulerEngine` was computing cron next-runs with `datetime.utcnow()` and ignoring each job's `timezone` field, so an IST cron fired 5.5h early. Now cron expressions are evaluated in the job's IANA timezone (`zoneinfo`) and stored as UTC. Added helpers `_tz_for`, `_now_utc`, `_iso_utc`, `_next_run`.
  - **`run_at` support**: `schedule_job` now accepts an absolute ISO `run_at` datetime for one-time sub-minute reminders (cron can't express seconds). `max_runs` defaults to 1 for `run_at` jobs.
  - **Configurable timezone + check interval**: added `SAGE_TIMEZONE` (default `Asia/Kolkata`) and `SAGE_SCHEDULER_CHECK_INTERVAL` (default 5s) so reminders fire promptly.
  - `schedule_job` input now has `timezone` and `run_at`; description documents both.
- **Files:** `app/scheduler/engine.py`, `app/scheduler/models.py`, `app/tools/builtin.py`, `app/application.py`, `app/config.py`, `tests/unit/test_scheduler_timezone.py` (new)
- **Why:** The user's luggage reminder would have fired at 14:30 IST instead of 09:00, and a "10 seconds from now" test couldn't be expressed in cron. Timezone-aware cron + `run_at` fixes both.

## A25 — Surface scheduled reminders to the user  *(2026-10-24)*

- **Area:** Scheduler / Notifications / CLI
- **Change:**
  - `CompletionNotifier` now also captures **scheduled reminder events** (`reminder` custom type + `scheduled_job_completed`/`scheduled_job_triggered`) and enqueues a user-facing line (`⏰ <name>: <message>`). Dedupes by `job_id` so a single trigger doesn't double-notify.
  - **CLI now polls for input with a 1s timeout** (`asyncio.wait_for`), draining pending notices (completions + reminders) on each idle cycle. Previously `input()` blocked, so a reminder only surfaced after the user typed something.
- **Files:** `app/notifications/completion.py`, `main.py`
- **Why:** The scheduler correctly fired and persisted the reminder event, but nothing surfaced it to the user — the notifier only watched employee-finished events, and the CLI blocked on `input()`. Reminders now appear live while the user is idle.

## A26 — Fix CLI stdin handling (single reader thread)  *(2026-10-24)*

- **Area:** CLI
- **Change:** Replaced the per-line `asyncio.wait_for(asyncio.to_thread(input, ...))` pattern with a **single persistent stdin reader thread** that pushes lines onto an asyncio queue. The main loop consumes the queue with a 1s timeout so background notices (completions, reminders) surface while idle.
- **Files:** `main.py`
- **Why:** The previous approach leaked a blocked thread on every 1s timeout; that thread `input()` would steal the user's typed line, so nothing the user typed was processed. A single long-lived reader thread fixes it — typed input reliably reaches the loop.

## A27 — Fix CLI prompt + `run_at` timezone shadowing  *(2026-10-24)*

- **Area:** CLI / Scheduler
- **Change:**
  - **CLI prompt restored**: the stdin reader thread now prints the "You: " prompt before each read (previously `iter(input, "")` called `input()` with no prompt, so the prompt never showed).
  - **`run_at` bug fixed**: `SchedulerEngine.add_job` used `timezone.utc`, but the `timezone` parameter (a string) shadowed the `timezone` module, raising `AttributeError: 'str' object has no attribute 'utc'` for any one-time `run_at` job. Now uses `UTC`.
  - **Steered toward `run_at`**: `schedule_job` description now tells SAGE to use `run_at` for one-time reminders (not date-specific cron, which fires every year and confuses users after the time passes).
- **Files:** `main.py`, `app/scheduler/engine.py`, `app/tools/builtin.py`, `tests/unit/test_scheduler_timezone.py`
- **Why:** The CLI "You:" prompt disappeared when the reader thread switched to `iter(input, "")`; and one-time `run_at` reminders crashed due to module shadowing. Both fixed. (Note: the "2027" next_run SAGE reported was correct cron behavior for a date-specific cron — steer one-time reminders to `run_at`.)

## A28 — CLI UI overhaul: cleaner rendering, sections, helpers  *(2026-10-24)*

- **Area:** CLI
- **Change:**
  - Rewrote `app/cli.py` with a clean `CliRenderer` surface: styled role prefix (`SAGE:`), a `SAGE: <text>` delta stream, tool-activity lines with adaptive duration formatting (`µs`/`ms`/`s`), and a structured stats footer (elapsed · tool calls · tokens · model).
  - Added command-output helpers: `section(title)`, `help_menu(rows)`, `tool_list(rows)`, `system_note(text)`, `notice(text)`, `error(text)` — replacing ad-hoc `print` calls in `main.py`.
  - `main.py` now uses those helpers; added `/jobs` and `/rules` commands; removed a dead `/approvals` block (the `/approvals` toggle is the live one); prompt sourced from `renderer.prompt`.
  - Removed duplicate/dead code that had accumulated (broken `Style`/method duplicates, undefined helpers).
- **Files:** `app/cli.py`, `main.py`
- **Why:** The CLI had grown messy (duplicate methods, broken prompt, ad-hoc prints). This is a focused readability overhaul: consistent section headers, adaptive tool durations, and a single renderer owning all output.

## A29 — Textual TUI (hermes-style interactive UI)  *(2026-10-24)*

- **Area:** CLI / UI
- **Change:**
  - Added `textual` dependency and a new `app/cli_tui.py` (`SageTui`) — a full-screen TUI with a scrollable conversation feed, persistent input box (↑/↓ history), status bar (active employees), and an immediate "thinking" indicator.
  - `main.py` now launches the TUI on an interactive TTY; otherwise it falls back to the ANSI `CliRenderer` (non-TTY/CI/`NO_COLOR`).
  - The TUI drives the **same `ConversationLoop`** through the thin callback contract (`on_delta`, `on_event`, `turn_start`, `turn_end`) — the core loop, employees, tools, scheduler, RAG, and permissions are untouched.
  - Removed the ANSI reader-thread stdin loop from the TUI path (Textual owns input).
- **Files:** `app/cli_tui.py` (new), `main.py`, `pyproject.toml`
- **Why:** The prior ANSI input model raced a background reader thread against streamed output (prompt timing bugs). A proper Textual TUI fixes input/rendering cleanly and gives a modern, readable interface.

## A30 — OS/desktop notifications  *(2026-10-24)*

- **Area:** Notifications / Scheduler / Tools
- **Change:**
  - New `app/notifications/os.py`: `os_notify(title, message, sound)` — macOS `osascript` (Notification Center), Linux `notify-send`, Windows PowerShell fallback; fail-safe (never crashes). Gated by `SAGE_OS_NOTIFICATIONS` (default true).
  - **`OsNotifier`** subscribes to the event bus and sends **silent** OS notifications automatically for scheduled jobs, proactive actions, reminders, employee-finished, and task-failed (mirrors the CLI's interest set).
  - New **`send_notification(title, message, sound=True)`** tool (SAGE-only) so SAGE can send a *sounding* OS notification for something important. Documented in `tools.md`/`authority.md` (tells SAGE not to double-notify reminders it already auto-sends).
- **Files:** `app/notifications/os.py` (new), `app/tools/builtin.py`, `app/application.py`, `app/config.py`, `app/conversation/tools.md`, `app/conversation/authority.md`, `tests/unit/test_os_notifications.py` (new)
- **Why:** Users want desktop notifications for scheduled jobs/reminders/proactive actions, plus a way for SAGE to nudge on something important. Auto (silent) for scheduled/proactive + SAGE-initiated (sounding) covers both.

## A31 — TUI approvals + chat area refactor  *(2026-10-25)*

- **Area:** CLI / UI
- **Change:**
  - **Approvals now work in the TUI.** Previously the TUI only wrote a passive "✋ approval required" line and never prompted the user. Added a **modal approval dialog** (`ApprovalModal`): shows tool + target, `y` approve / `n`/`esc` deny. `main.py` passes an `approval_callback` that calls `app.approve_pending`/`deny_pending` (same as the ANSI path). The TUI loops while `response.pending_approval`.
  - **Chat area refactor.** User messages written as `┃ You:` blocks, SAGE replies as `┃ SAGE:`; separator lines between turns; tool/approval activity as compact dim lines. Clear visual separation between messages.
  - **Side panel** (right column) toggled with **Tab**, showing the current status provider output (active employees / model).
  - **Thinking animation** uses a cycling spinner task.
- **Files:** `app/cli_tui.py`, `main.py`
- **Why:** The TUI couldn't approve actions (a real functional gap — `run_command` approvals went nowhere). The chat area is also more readable with clear message separation and a toggleable status panel.

## A32 — Reminders/scheduled/proactive notifications now play sound  *(2026-10-25)*

- **Area:** Notifications
- **Change:** `OsNotifier` now sends **sounding** notifications for reminders, scheduled jobs, and proactive actions (`_SOUND_EVENTS` set), matching the SAGE-initiated `send_notification` behavior. Employee completions and task failures stay silent (avoid noise).
- **Files:** `app/notifications/os.py`, `tests/unit/test_os_notifications.py`
- **Why:** Earlier the auto-notifier sent everything silently, so a scheduled reminder produced no chime even though `send_notification` did. Users expect an audible nudge for reminders and scheduled work.

## A33 — Chat area: YOU / SAGE labels with divider lines  *(2026-10-25)*

- **Area:** CLI / UI
- **Change:** The TUI chat feed now renders each message as a bold `YOU:` / `SAGE:` label followed by a full-width divider line, then the message text — a cleaner "conversation log" look instead of inline prefixes.
- **Files:** `app/cli_tui.py`
- **Why:** User requested a clearer chat layout with the sender label and a rule under it for each message.

## A34 — Markdown support in the TUI chat  *(2026-10-25)*

- **Area:** CLI / UI
- **Change:** The TUI chat now renders **SAGE messages as Markdown** (via Textual's `Markdown` widget — bold, code, lists, headings render properly). The feed is a scrollable `Vertical` container that appends message widgets: user messages as `YOU:` label + plain text, SAGE messages as `SAGE:` label + `Markdown` body, with divider rules between turns. Tool/activity lines and notices are compact rich-text lines. Auto-scrolls to the newest message.
- **Files:** `app/cli_tui.py`
- **Why:** SAGE replies often contain code, lists, and formatting that a plain rich-text log renders as raw markup. A `Markdown` widget per message makes replies readable.

---

# Part B — Proposed: Employee Workers

**Status: built (2026-10-21).** The direction below was implemented as the
Worker-Employee system (`app/employees/`) and recorded as **D28** in
`DECISIONS.md`; the old process-worker system was deleted. Kept here as the
design record.

This is a *new layer above* the existing executor-worker, not a rewrite of it
(see D24).

## B1 — The vision: SAGE as manager butler, workers as employees

- **SAGE** is the single trusted principal — the manager butler. It is the only
  agent the user talks to. It plans, delegates, narrates, and asks for approval.
- **Workers** are named, always-on employees: **`alpha`, `charlie`, `Asta`,
  `Max`, `Johnny`** — exactly five, no more, no less. They stand by and work
  **only when SAGE assigns something**. In practice ≤ 2 run concurrently.
- Each employee is an **agent** with its own (slim) reasoning loop and a
  **role card** — not a diminished copy of SAGE, and not the current dumb
  subprocess either.
- Employees **report to SAGE** and **ask SAGE for permission**; they never talk
  to the user directly. SAGE remains the single point of contact so the "one
  butler" coherence is preserved.

## B2 — Two kinds of worker

| | Executor-worker (exists, D24) | Agent-worker (proposed) |
|---|---|---|
| Mind | none — no LLM | own reasoning loop + role card |
| Input | one `TASK` (tool + args) | a goal / objective |
| Output | one raw `RESULT` | a completed outcome + report |
| Trust | not trusted; all judgment SAGE-side | scoped trust within a mandate |
| Cost | cheap, finite | LLM calls, needs budgets |

Both can coexist: trivial work → executor-worker; complex, multi-step work →
agent-worker. SAGE chooses.

## B3 — What each employee is

An employee is a `SAGEApplication`-style composition with:

- its own tool registry (a **filtered subset** of SAGE's),
- its own role card (a short mandate: "you are a research employee; you have
  these tools; return a concise finding") — **not** richer personality,
- a **capability grant** scoped to the assignment,
- hard budgets: max iterations, max tokens, wall-clock, and a kill switch.

Reused building blocks: `ConversationLoop`, `PermissionEngine` (central
policy), `EventBus` (SAGE↔employee), `TaskStore` (work items),
`soul.md`/`authority.md` as per-employee role cards.

## B4 — Permissions direction (deliberate philosophy change)

- **Long-term goal:** reduce approval overhead — **SAGE itself decides** whether
  a given task/job needs approval (leaning on LLM judgment), and the
  `PermissionEngine` becomes a **final safety checkpoint** to stop genuinely
  dangerous actions.
- **Today's model** (D1, D22) is the opposite: deterministic metadata decides,
  the LLM never does. This proposal **relaxes that principle on purpose** —
  worth an explicit decision entry when implemented.
- **Until then:** employees inherit a **narrower-than-SAGE** capability grant;
  approvals still route through SAGE to the user.

## B5 — Open questions (resolve before building)

1. **Autonomy ceiling** — bounded multi-step toward a goal (recommended) vs
   single bounded objective?
2. **Persistence** — ephemeral (spawn per goal) vs long-lived named colleagues
   with memory of past assignments? Named roster suggests persistence, but start
   simple.
3. **Tool registry** — shared registry with a per-employee filter (recommended).
4. **Concurrency** — fixed at 5 standbys; how many may run at once (≤ 2 in
   practice)?
5. **UI** — how the roster + activity is presented (deferred).

## B6 — Note: `max_workers` is obsolete

Because the roster is a **fixed five, always-on, standby** employees, the
generic concurrency-cap concept (`settings.max_workers` in `app/config.py`,
currently unused) is **not** the right mechanism. The cap is structural — there
are simply five employees. Do not build a dynamic pool/scaling system for this
(architecture §52–54 scaling is superseded by the fixed roster).

---

# Part C — Worker Hardening Backlog

Improvements to the **existing executor-worker** (independent of the employee
vision), to be scheduled.

1. **Kill the process group on timeout.** `ProcessExecutor` spawns the worker,
   but `run_command` uses `create_subprocess_shell` (a `/bin/sh -c …`
   grandchild). On deadline only the worker PID is killed, so grandchildren can
   orphan. Fix: `start_new_session=True` + `os.killpg` (SIGTERM first, then
   SIGKILL). Files: `app/execution/executor.py`, `app/tools/shell_tools.py`.
2. **Enforce `resource_limits` inside the worker.** The TASK sends
   `deadline_seconds` but `worker.py` ignores it — no wall-clock self-kill and
   no rlimits. Enforcement currently relies solely on the SAGE side.
3. **Publish `task_*` / `worker_*` events to the `EventBus`.** The dispatcher
   only writes to SQLite and logs locally, so the worker panel's live feed is
   empty and it falls back to REST polling. Give the dispatcher an `event_bus`
   and emit `task_created/started/completed/failed`. File:
   `app/execution/dispatcher.py`.
4. **Clean up `_background` on completion; await outstanding tasks on
   shutdown.** The dict grows unbounded over a long session, and in-flight
   workers can be orphaned on stop. File: `app/execution/dispatcher.py`.

*(The former "enforce `max_workers`" item is dropped — see B6.)*
