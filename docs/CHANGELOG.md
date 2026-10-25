# SAGE Change Log

A running, chronological record of changes to SAGE's architecture and behavior.

- **Design rationale (older, formalized decisions):** [DECISIONS.md](DECISIONS.md)
- **How the running system works:** [HOW_IT_WORKS.md](HOW_IT_WORKS.md)
- **Full target architecture:** [../ArchitectureV2.md](../ArchitectureV2.md)

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
