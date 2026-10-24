# Decision Log

Why SAGE is built the way it is. How it works: [HOW_IT_WORKS.md](HOW_IT_WORKS.md).
Recent changes and proposed directions: [CHANGELOG.md](CHANGELOG.md).

## Context

The goal was to make SAGE conversational and capable by learning from Hermes'
agentic loop (source: `~/codes/hermes-agent`), while keeping SAGE's own
architecture (ArchitectureV2.md): SAGE is the authority, the LLM never is.

---

## D1 — Adopt Hermes' mechanisms, keep SAGE's authority model

**Decision:** Copy the *shape* of Hermes' loop (iterate: LLM → tools → LLM
→ final), its compaction algorithm, and its tool-registry pattern. Do **not**
copy its permission/approval model. Every tool call in SAGE goes through the
deterministic `PermissionEngine` before execution.

**Why:** Hermes trusts its own guardrails and hook chain inside one process.
SAGE's core principle (ArchitectureV2 §26) is that the LLM cannot declare an
action safe — permission comes from tool metadata, deterministically. The
loop is the natural interception point.

## D2 — Hybrid execution: inline + `start_task`, not Worker processes yet

**Decision:** Small tools run inline in the loop. Substantial work goes
through the `start_task` tool: a durable `tasks` row + an asyncio background
task, returning a `task_id` immediately. "How's task A going?" is a normal
turn that reads `task_status` from SQLite.

**Why:** The requirement was "SAGE must always be free" — the conversation
can never block on a running task. Real Worker processes (UDS, separate
processes, leases — ArchitectureV2 §7–14) are the final design, but building
them now would delay the loop and compaction, which everything else depends
on. The dispatcher interface (`start`/`get_status`) is exactly the seam
where the Worker pool plugs in later — the loop and tools do not change.

## D3 — Raw history is append-only; compaction only changes the view

**Decision:** The `messages` table is permanent. The context summary lives in
`session_state`, not as a rewrite of message rows. Compaction produces a
*derived* view (summary row + tail) for the API call only.

**Why:** Matches ArchitectureV2 §30 (raw history is permanent source
material). Hermes rewrites/marks DB rows during compaction; that couples
durable storage to one LLM's context management and makes recovery harder.
Keeping storage and view separate is simpler and safer. Trade-off: the raw
table grows forever — acceptable locally, and pruning/archival can come
later.

## D4 — Iterative summary with a deterministic fallback

**Decision:** Compaction merges *previous summary + new middle* with a single
aux LLM call (structured prompt: goal/state/decisions/errors/files). If the
LLM call fails, a static summary is built from recent user messages. Two
ineffective compactions in a row ("strikes") block further attempts.

**Why:** Straight from Hermes (`context_compressor.py`): re-summarizing the
whole history from scratch is slow and loses information; iterative merging
is fast and stable. The fallback means a summarizer outage degrades summary
quality but never loses the conversation. Anti-thrash strikes stop the
expensive failure loop Hermes guards against.

## D5 — Trigger at 50% of the context window; cheap token estimates

**Decision:** Compaction triggers when estimated prompt tokens reach
`context_window × 0.5`. Tokens are estimated as `chars / 4` — no real
tokenizer.

**Why:** 50% is Hermes' default and proven in practice. A real tokenizer
(e.g. tiktoken) adds a heavy dependency and per-provider mismatch; chars/4
is accurate enough for a threshold, and compaction's effectiveness is judged
by whether the prompt actually dropped under the threshold.

## D6 — Removed Hermes' deterministic pre-pass pruning

**Decision:** No separate "prune old tool results" pre-pass. Tool results in
the middle are truncated (500 chars) only when formatting the *summarizer*
input.

**Why:** Hermes prunes old tool results because they stay in the window as
compacted rows. In SAGE's design the middle never reaches the main API call
— it only feeds the summarizer — so the pre-pass would do nothing useful.
Honest simplification, not an oversight.

## D7 — Deferred: `api_content` sidecar, prompt-cache breakpoints, streaming

**Decision:** The `api_content` column exists in `messages` but is unused.
No prompt-cache breakpoints, no streaming, no memory injection yet.

**Why (in order of dependency):**
- The sidecar's purpose is byte-stable replay of *injected context* (Hermes'
  memory blocks). SAGE has no injected context yet — nothing to replay.
- Cache breakpoints matter once steady-state latency is a concern; the
  summary-row design already keeps the prompt prefix stable (system prompt +
  summary change rarely).
- Streaming is a UX layer over the loop; the loop returns final text today.

## D8 — Replaced the one-shot decision engine; left legacy modules in place

**Decision:** `ConversationLoop` is the new `conversation_engine` in
`application.py`. `app/decision/` and `app/response/` are untouched but no
longer wired.

**Why:** The old engine made exactly one LLM call that returned JSON
"respond or use_tool" — no tool chains, no history, no recovery. Rewriting
it in place would churn tests for no benefit; deleting it now would break
tests we can migrate deliberately later. It is dead code kept temporarily.

## D9 — Permission denials are tool results, not exceptions

**Decision:** When the PermissionEngine returns APPROVAL_REQUIRED/DENIED,
the loop appends `{"success": false, "error": "Permission approval_required: ..."}` as
the tool result and lets the model continue. The background dispatcher
raises and marks the task failed.

**Why:** The model needs to *explain* the denial to the user, which it can
only do if the denial comes back as context. Raising would abort the turn
with no user-facing explanation. No approval flow exists yet — that arrives
with the Operation Manager (ArchitectureV2 §27 approval binding).

## D10 — No migration framework; `CREATE TABLE IF NOT EXISTS` at startup

**Decision:** `app/persistence/schema.py` runs idempotent DDL on startup.

**Why:** One developer, one database, evolving schema — Alembic-style
migrations are overhead with no payoff yet. The moment schema exists in
production data, migrations become mandatory. Noted as tech debt.

## D11 — Test infrastructure: scripted fake provider + DB fixtures

**Decision:** `FakeInferenceProvider` accepts a `scripted` list of
`InferenceResponse` (including tool_calls) and records requests. Tests use
async fixtures that always close the aiosqlite connection.

**Why:** Scripted responses make loop behavior deterministic without any
network. The fixture discipline is not optional: a leaked aiosqlite
connection keeps its worker thread alive and pytest hangs at exit — this
cost real debugging time; do not regress it. Capability flags on the fake
matter: the router rejects providers that lack required capabilities
(e.g. `reasoning=True` for the summarizer).

## D12 — One live smoke test against the real provider

**Decision:** After unit tests passed, one real end-to-end turn ("What time
is it? Use the tool.") was run against the configured NVIDIA provider to
verify tool-calling, the permission path, and persistence with a real model.

**Why:** Unit tests with scripted responses can hide contract mismatches
(e.g. provider tool_calls shape). One request is cheap insurance. The run
succeeded: tool round in DB roles `user → assistant(tool_calls) → tool →
assistant`, correct final answer.

---

# Phase 3 — Memory, search, files

## D13 — Explicit agent-curated memory, not automatic extraction

**Decision:** Long-term memory is a bounded `data/memory.md` (4000 chars,
markdown bullets) edited explicitly by the model through the `memory` tool.
No background LLM extraction pipeline.

**Why:** Hermes uses both (explicit files + background provider extraction).
Extraction pipelines cost a per-turn LLM call and can hallucinate "facts".
Explicit writes keep memory trustworthy and the critical path free. The
`MemoryProvider` seam from Hermes can be added later without changing the
tool or the store. Memory is injected into the system prompt by
`ContextBuilder` each turn — the file is tiny, so reading it is cheaper
than managing a cache, and cache breakpoints are already deferred (D7).

## D14 — FTS5 for recall, no embeddings

**Decision:** `messages_fts` (SQLite FTS5) indexes raw history on every
append, with a one-time backfill for pre-existing rows. Exposed as the
`session_search` tool.

**Why:** ArchitectureV2 §34 specifies FTS5 first, no vector database.
Hermes proves this is enough (~20ms queries, no embedding dependency).
Embeddings can be layered on later behind the same tool.

## D15 — Read-only file tools only

**Decision:** `read_file` (line offset/limit, 100K char cap) and
`list_files` (200 entries cap). Nothing that writes, moves, or deletes.

**Why:** Reading unblocks the obvious use case (SAGE inspecting the
machine) with zero safety surface. Write/patch tools need the approval
flow and Operation Manager first (ArchitectureV2 §26-27) — a write tool
today would either bypass the permission model or be unusable.

---

# Phase 4 — Write tools, approvals, operations

## D16 — Approval is bound data, not a boolean

**Decision:** When a tool call needs consent, the loop records a pending
row in `approvals` bound to the exact tool name + arguments + resource,
with a 10-minute expiry. Approving executes *only the bound arguments* —
never new input. The turn ends with `pending_approval` on the response;
the caller (CLI, later Web) prompts the user and calls
`approve_pending`/`deny_pending`.

**Why:** ArchitectureV2 §27 forbids a bare `approved = true` flag — a
boolean can be replayed against different arguments. Binding arguments at
request time makes consent specific and tamper-proof. Expiry prevents
stale consents from lingering.

## D17 — Dynamic `requires_approval` hook on tools

**Decision:** `ToolDefinition` may declare a deterministic
`requires_approval(arguments) -> bool` check. `write_file` uses it:
creating a new file is ALLOWED inline; overwriting an existing file
requires approval. Static metadata stays static; argument-dependent risk
is decided by a pure function, never the LLM.

**Why:** Hermes does the same (read-before-write guards). A static
"write is always destructive" rule would make the common case (create a
file) needlessly friction-heavy; a static "never destructive" would
silently clobber data. The hook keeps the permission model generic —
policy still only sees `destructive=True/False`.

## D18 — Operations track approved and crash-relevant side effects

**Decision:** Every execution through the approval path (and later,
background tasks) runs via the Operation Manager: an `operations` row is
recorded *before* execution (`EXECUTING`), then `SUCCEEDED`/`FAILED`.
On startup, leftover `EXECUTING` rows become `UNKNOWN`.

**Why:** ArchitectureV2 §12/§15-17: after a crash, SAGE must know which
real-world actions may have happened and not blindly re-run them. UNKNOWN
(resolved to safe defaults by future verification) is the honest status
when the outcome cannot be determined. Inline allowed writes stay
cheap (no operation row) — they're idempotent creations; operations
cover the cases where a crash mid-flight actually matters.

## D19 — Relative file paths anchor to a workspace root

**Decision:** Relative paths in `read_file`/`write_file`/`list_files`
resolve under a configured workspace directory (`SAGE_WORKSPACE`, default
`data/workspace`), never the process cwd. Missing parent directories are
created on write. The system prompt states the workspace path and tells
the model to use descriptive names with extensions.

**Why:** Mirrors Hermes (`tools/file_tools_paths.py` — relative paths
anchor to the authoritative workspace root, not process cwd). Live test
exposed the bug: "in a new file" produced a bare `newfile` in whatever
directory the CLI happened to start in — unpredictable and useless.
Anchoring makes results reproducible and keeps stray files out of the
project tree.

---

# Phase 5 — Streaming

## D20 — Stream all turns, accumulate before persisting

**Decision:** Every LLM call runs with `stream=True`. Content deltas are
delivered live via an `on_delta` callback (`handle_stream`); the full
response is accumulated and only then persisted/appended — persistence
format is unchanged. Tool-call deltas are accumulated inside the provider
and exposed only once, complete, at stream end. Fallback to the next
provider happens only **before the first delta** reaches the user;
mid-stream errors raise (never re-emit visible text).

**Why:** Streaming only "final answers" isn't possible — the loop doesn't
know if a response will contain tool calls until it's complete, and
tool rounds often carry user-visible commentary ("Let me check the
time...") that is worth showing live. Accumulate-then-persist keeps the
append-only history exact (no partial rows), and the before-first-delta
fallback rule guarantees the user never sees duplicated text after a
provider switch. Providers without SSE support degrade gracefully via the
base-class `stream()` (single delta from `generate()`).

---

# Phase 6 — CLI experience

## D21 — Event-driven CLI, logs to file, zero new dependencies

**Decision:** The loop exposes an `on_event` hook emitting `LoopEvent`s
(`llm_start`, `tool_start`, `tool_end`, `approval`, `compaction`).
`app/cli.py` renders them: spinner while the model thinks, `⚙ tool ✓/✗`
lines for tool rounds, approval markers, a per-turn stats footer.
Slash commands: `/help`, `/tools`, `/memory`, `/search`, `/exit`.
INFO logs go to `data/sage.log`; the console only shows warnings+.

**Why:** The agent-feel comes from *events the loop already knows* —
no scraping stdout, no extra deps (plain ANSI; disabled when not a TTY
or `NO_COLOR` is set). Logging straight to stdout fought with streaming
output and the spinner; a log file keeps full observability without
noise. The CLI/Web layers stay thin consumers of the same event stream,
so the future WebSocket UI gets identical information for free.

---

# Phase 7 — Real capability: shell + patch

## D22 — Capability before infrastructure; deterministic command gating

**Decision:** New tools first (not Workers/UI):
- `run_command` — shell execution in the workspace cwd, hard timeout
  (1–300s), 20K output caps, `worker_eligible` so `start_task` can run
  long commands in the background through the existing dispatcher.
- `patch` — exact-text surgical replacement (single match by default,
  `replace_all` opt-in) instead of rewriting whole files.
- **Command classifier** (`app/permissions/command_policy.py`): a
  command auto-runs only if its first token is on a read-only allowlist
  (`ls`, `cat`, `grep`, `git status`, …) AND it contains no shell
  metacharacters (`; | & < > \` $(` newline). Everything else → the
  approval flow. Deterministic; never the LLM's call.
- **Workspace trust**: edits (write/patch) to existing files *inside*
  the workspace run immediately; existing files *outside* need consent.
  New files never need consent.

**Why:** SAGE could chat and edit files but couldn't *do* anything —
run builds, git, checks. Workers are infrastructure ahead of use until
real commands exist to isolate; the dispatcher seam means Workers later
only change the dispatcher. The classifier mirrors ArchitectureV2 §26
(deterministic metadata decides, not the model): allowlisting first
tokens without a metacharacter check would be trivially bypassable
(`ls; rm -rf /`), so both conditions are required. Workspace trust keeps
the common loop (edit my scratch files) frictionless while gating the
user's real files.

---

# Phase 8 — Task verification

## D23 — Verify outcomes, don't trust executors

**Decision:**
- **Deterministic verification** (`app/execution/verification.py`): after a
  background task or approved operation executes *successfully*, SAGE
  re-checks the real world state and records `verification` on the
  task/operation — `passed` / `failed` / `unverified` (D23):
  - `write_file` → file exists AND content == requested content
  - `patch` → file exists, old text gone, new text present
  - `run_command` → exit 0, no error payload (timeout = failed)
  - anything else → `unverified` — reported honestly, never upgraded to
    `passed`
  A `failed` verification marks the task `failed` / the operation `FAILED`
  even though the tool itself "succeeded" — the executor's claim is not
  trusted (ArchitectureV2 §60–61). No LLM verifier yet: deterministic
  checks cover everything we run today; the LLM-verifier rung of §60 stays
  for genuinely unverifiable work later.
- **Approval gate at `start()`**: the target tool's dynamic
  `requires_approval` hook runs *before* a background task is created (and
  again in `_run` as defense in depth) — a state-changing command cannot
  be laundered through `start_task` to skip consent. Refusal tells the
  model to run it inline first, where the normal approval flow applies.
- **Additive schema**: `tasks.verification` + `tasks.verification_detail`,
  added idempotently via `PRAGMA table_info` + `ALTER TABLE` at startup
  (still no migration framework — `migrations/` stays empty).

**Why:** §62 says SAGE *verifies, interprets, organizes* Worker results —
verification is the trust boundary that makes Workers (and today's
background tasks) safe to believe. Checking existence/content/exit codes
is cheap, deterministic, and catches the real failure mode: silent
no-ops that still claim success. The `start()` gate closed a real hole:
the dispatcher previously only re-ran the *static* permission check, so a
dangerous background command would have run without consent.

---

# Phase 9 — Workers: separate processes over UDS

## D24 — Process-per-task workers behind the dispatcher seam

> **Superseded by D28.** The process-worker system (`app/execution/worker.py`,
> `executor.py`, `protocol.py`, `dispatcher.py`) was removed and replaced by
> the Worker-Employee system (`app/employees/`). This entry is kept for the
> reasoning that still holds (honest `unknown`, SAGE-side trust, the seam).

**Decision:**
- **Worker = separate OS process**, one per task: `python -m app.execution.worker`
  connects to a per-task Unix Domain Socket, receives exactly one `TASK`
  message, executes the tool with its own local registry, and returns the
  raw outcome (`RESULT` / `ERROR`) (ArchitectureV2 §9–11). One-shot means
  §9 cleanup is satisfied by exiting.
- **Versioned wire protocol** (`app/execution/protocol.py`): newline-framed
  JSON with the full §11 envelope (`protocol_version`, `message_type`,
  `event_id`, `task_id`, `worker_id`, `sequence_number`, `timestamp`,
  `payload`), 1 MB size cap, schema validation, `ProtocolError` on any
  violation. v1 types: `TASK` (SAGE→worker), `RESULT`/`ERROR` (worker→SAGE).
- **Executor seam** (`app/execution/executor.py`): `TaskDispatcher` now
  depends on a `TaskExecutor` — `ProcessExecutor` in production (spawns the
  worker, owns the UDS dance, deadlines = tool timeout + 30 s grace),
  `InlineExecutor` (in-process) kept for fast unit tests. Nothing outside
  the dispatcher/executor knows whether tasks run in-process or out.
- **Honest `unknown` status (§12):** never spawned / never connected →
  `failed` (provable: `TASK` was never sent, so the tool never ran);
  `TASK` sent but no usable answer (worker died, deadline hit, broken
  frame) → `unknown` — recorded, never silently reported as failed and
  never blindly retried. Startup recovery marks leftover
  `queued`/`running` tasks `unknown` (§47), mirroring OperationManager.
- **Trust stays SAGE-side:** static + dynamic permission checks run
  *before* dispatch (including the new `worker_eligible` gate: tools that
  aren't background-safe cannot go through `start_task`), and
  deterministic verification (D23) re-checks the real outcome of every
  raw `RESULT`. The worker never sees permissions or memory.
- **Sockets live in** `tempfile.gettempdir()/sage-workers-<uid>/` — AF_UNIX
  paths cap at ~104 chars (macOS), so in-repo paths are unsafe; stale
  sockets are harmless (task ids are unique, each path is unlinked before
  bind).

**Why:** the whole point of the dispatcher seam was that Workers replace
only the dispatcher — this is that swap, with the loop, tools, approvals
and verification untouched. Isolation means long commands run off SAGE's
event loop in a killable process (deadline → `UNKNOWN`, not a hung turn).
Deferred (noted, not needed yet): pools/scaling §52–54, leases §13,
atomic cross-worker claiming §14 (single SAGE assigns), PROGRESS messages,
LLM verifier (§60's second rung).

---

# Phase 10 — Provider junk & tool-call hygiene

## D25 — Sanitize tool calls at the boundary; no needless tools

**Decision:**
- **Provider-boundary sanitization** (`openai_compatible.py`):
  reasoning models leak chat-template text (`<|channel|>commentary`)
  into tool-call fields. Names are cut at the first `<|` (the real name
  always precedes the leak) and filtered to registry-safe characters;
  arguments are salvaged to the first *balanced, JSON-valid* `{...}`
  slice (string-aware scan — naive `rfind` grabbed the wrong brace).
  Applied on both the streaming and non-streaming paths.
- **Unique-prefix recovery in `ToolRegistry.get`**: if a name isn't an
  exact match but exactly ONE registered tool is a prefix of it, that is
  unambiguously the intended tool (covers junk glued *without* a `<|`
  marker, e.g. `start_taskcommentary`). The recovered tool then goes
  through every permission check like any other lookup; ambiguity or no
  match stays a hard `Unknown tool` error.
- **System prompt**: greetings/small talk → plain text, no tools; never
  repeat a failed call unchanged.

**Why:** the boundary (provider JSON → our loop) is the one place where
foreign text enters tool dispatch, so it is sanitized once there instead
of being defended at every consumer. Prefix recovery is deterministic —
still no fuzzy matching or LLM judgment. Live-verified: "hi" now gets a
plain reply with 0 tool calls (it previously fired `start_task` twice
with a polluted name).

---

# Phase 11 — Provider switch to Nebius (latency fix)

## D26 — Primary provider is now Nebius DeepSeek-V4-Flash-0731

**Decision:**
- **Model catalog reordered**: Nebius `deepseek-ai/DeepSeek-V4-Flash-0731`
  (priority 90, tool-calling ✓, streaming ✓, reasoning ✗) is now the
  primary chat model; NVIDIA `openai/gpt-oss-20b` (priority 80, reasoning ✓)
  remains as fallback and handles compaction/summary turns.
- **Provider factory** extended with `nebius` branch using
  `NEBIUS_API_KEY` / `NEBIUS_BASE_URL` from settings (already in `.env`).
- **ANSI escape stripping** on CLI input (`^[[A` etc.) so paste/up-arrow
  garbage never reaches the model.
- **Prompt tweak**: `session_search` at most once per question.
- **Latency result**: single-turn "hi" went from ~12 s → <1 s; memory
  turn ~26 s → <1 s; search turn ~69 s → <1 s. Tool calling works
  (list_files, run_command); memory/search tools are skipped when the
  model can answer from immediate context (expected behaviour for short
  conversations).

**Why:** NVIDIA's free gpt-oss-20b endpoint is slow (~10–20 s/turn) and
reasons by default with no `reasoning_effort` control. Nebius
DeepSeek-V4-Flash is purpose-built for speed; the fallback ensures
reasoning-heavy tasks (compaction) still work. The provider seam made
this a catalog-order change only.

---

## D27 — Web backend drives the real engine; one composition root

**Decision:**
- `web/backend.py` (FastAPI) reuses the **same `SAGEApplication`** as the
  CLI. There is exactly one composition root; the web layer is a thin
  adapter, not a second implementation.
- **Chat is a first-class endpoint.** `POST /api/chat` and the
  `/ws/sage` WebSocket `chat` message both call
  `conversation_engine.handle` / `handle_stream`. The WebSocket path
  forwards each text delta as `chat_delta` and finishes with `chat_done`
  carrying `content`, `input_tokens`, `output_tokens`, `model`, and
  `pending_approval`.
- **Every bus event is broadcast** to all connected sockets: the lifespan
  subscribes a handler via `event_bus.subscribe_all(...)` that forwards
  `event.to_dict()` as `{"type": "event", ...}`. The frontend refreshes
  events/approvals on receipt.
- **Approvals work over the socket** (`approve`/`deny` messages) with a
  REST fallback at `/api/approvals/{id}/approve|deny`. The action body is
  optional so the UI can POST with no payload.
- **Scheduler/proactive aliases**: the frontend calls
  `/api/scheduler/rules`; the backend exposes both that and
  `/api/proactive/rules` backed by the same handlers.
- **Model outputs are normalized** with `_model_dump` (dataclass/pydantic
  aware) so jobs, rules, approvals, and events serialize cleanly.

**Why:** The web UI must exercise the real agent — memory, tools, workers,
approvals, scheduler — not a mock. Sharing `SAGEApplication` keeps
behaviour identical between CLI and web and avoids drift. Live-verified:
`POST /api/chat` → `Good afternoon, Sir. …` with
`11934 in / 75 out / deepseek-ai/DeepSeek-V4-Flash-0731`, and the
WebSocket stream delivered live deltas then a final `chat_done`.

---

# Phase 12 — Worker-Employee system

## D28 — Employees replace process-workers; SAGE judges, the engine backstops

**Decision:**
- **The process-worker system is removed, not wrapped.** `app/execution/worker.py`,
  `executor.py`, `protocol.py`, and `dispatcher.py` are deleted. The old
  design — one OS subprocess per task over a Unix socket, exactly one tool,
  raw result, **no mind** — is replaced by a new layer (`app/employees/`).
  D24/§52–54 scaling, pools, and worker leases are obsolete with it.
- **An employee is an agent, not a subprocess.** The roster is a fixed five —
  **Asta, Otto, Tony, Alpha, Charlie** (lowercase ids `asta`…, `employee-asta`,
  cased display names). Each employee has its own `ConversationLoop`, a
  filtered tool registry (`employee_eligible` tools), its own memory and
  workspace (`data/employees/<key>/`), and a slim role card — a mandate, not
  a personality. Employees **report to SAGE** and never address the user;
  SAGE stays the single point of contact.
- **Identity-persistent, process-lazy (N3).** The roster is permanent; an
  employee's loop is built only when it is first assigned work, runs as a
  lazy asyncio task inside SAGE's process, then idles. No idle burn, no
  dynamic pool.
- **SAGE dispatches goals, not tools.** `start_task(tool, args)` becomes
  `assign_task(worker, objective)`. The frozen contract SAGE depends on is
  `assign(worker, objective) -> task_id`, `get_status(task_id) -> dict`,
  `recover() -> int`; `task_status` keeps every old key (additive only).
  `Supervisor` (the seam) replaces `TaskDispatcher`.
- **Per-assignment hard budgets are mandatory (N8).** `max_iterations`,
  `max_tokens`, `max_wall_seconds`, and a kill switch. A breach ends the
  assignment honestly — `failed`, or `unknown` when the outcome is genuinely
  indeterminate. Leftover `queued`/`running` rows from a previous process
  recover to `unknown` (§12/§47, unchanged).
- **Permission philosophy change (N5).** The original principle (D1/D22) was
  *"the LLM never declares an action safe."* We are moving to **SAGE judges;
  the `PermissionEngine` is a final safety checkpoint** rather than the sole
  arbiter. Rationale: approval overhead does not scale to N autonomous
  employees. Guardrails that keep this honest:
  - an **immutable hard-block list** the engine enforces *before* anything
    else, which SAGE cannot override — filesystem destruction outside the
    workspace sandbox, writes escaping the sandbox root, exfiltration of
    secrets to the network, and privilege escalation;
  - **post-hoc verification** of real outcomes stays (D23);
  - every permission decision is **logged** for audit.
- **Consequence already shipped (A5):** file edits (`write_file`, `patch`)
  never require approval — the engine only backstops the hard-block list.
  `remove_job`/`remove_rule` are likewise non-destructive.

**Why:** the manager-butler model — one trusted principal the user talks to,
with named employees doing bounded autonomous work — is the only shape that
scales to more background work without an approval storm, and it replaces a
mindless subprocess with something that can actually pursue an objective.
`HOW_IT_WORKS.md` documents the frozen contract and the runtime.

**Open (deferred):** the second rung of the hard-block list (an LLM verifier),
per-employee tool allowlists layered on `employee_eligible`, and the UI
roster/activity panel (Phase 9, API already exposed at `/api/workers*`).

---

## Phase 13 — Agent Backends for Employees

### D29 — Employees invoke external agents (OpenCode, Hermes) via tools

**Decision:**
- Employees get `run_opencode` and `run_hermes` tools (`employee_eligible=True`,
  `requires_approval=True`). These wrap the respective CLIs:
  - `opencode run --continue` / `--session <id>` / `--model` / `--dir`
  - `hermes chat -Q --format stream-json --continue` / `--resume <id>` / `--model` / `--in <dir>`
- **Session continuity**: Each employee tracks sessions in `agent_sessions` table.
  New workspace → new session; same workspace → resume via stored external session ID.
- **SAGE cancellation**: `cancel_task` tool (SAGE-only, not employee_eligible) lets
  SAGE terminate any running background task. Replaces wall-clock timeout.

**Why:** Employees were limited to local file/shell tools. Real coding needs a
  proper agent (OpenCode); research/analysis needs Hermes. The CLI approach
  avoids reimplementing agent loops and leverages existing tooling. Session
  tracking enables "continue where you left off" across assignments.

### D30 — Unlimited employee budgets; SAGE intervenes via cancellation

**Decision:**
- `employee_max_wall_seconds` default: `0.0` (unlimited). Iterations and tokens
  already unlimited (0). The only runaway guard is now SAGE's explicit
  `cancel_task`.
- `BudgetTracker` in `app/employees/budgets.py` no longer enforces wall-clock
  by default (0 = unlimited). Wall-clock check only triggers if explicitly set.

**Why:** OpenCode tasks can legitimately take 20-30 minutes on complex
  refactoring. A 10-minute hard timeout was killing valid work. SAGE (the
  manager) should decide when to intervene, not a timer. The `cancel_task`
  tool gives SAGE that control.

### D31 — Agent session persistence in SQLite

**Decision:**
- New table `agent_sessions` tracks: `session_id` (internal), `employee_key`,
  `backend` (opencode/hermes), `workspace_path`, `objective`, `external_session_id`,
  `status`, timestamps.
- `AgentSessionManager.get_or_create_session()` resumes existing session for
  same employee+backend+workspace if active; else creates new.
- On completion, `external_session_id` updated from agent output for future resume.

**Why:** Both OpenCode and Hermes support session resume via CLI flags.
  Without tracking, every employee task would start fresh, losing context.
  The table is per-employee (each has their own `sage.db`), so sessions
  are naturally isolated.

---

## Phase 14 — Permission Model Fixes & Employee Agent Integration

### D32 — Read-only commands and employee agent tools skip approval

**Decision:**
- `run_command` with read-only commands (allowlist + no metacharacters) → `ALLOWED` without approval
- `run_opencode` / `run_hermes` when invoked by an employee → `ALLOWED` without approval
- Same tools when invoked directly by SAGE → `APPROVAL_REQUIRED` (SAGE direct call still needs consent)

**Implementation:**
- `PermissionEngine._is_read_only()` expanded to return `True` for agent tools when `current_employee_key()` is set
- `run_opencode` / `run_hermes` classification changed: `EXTERNAL_SIDE_EFFECT` → `LOCAL_MUTATION` (isolated workspace, budgeted, verified)
- Dynamic hook `_agent_requires_approval` now checks `current_employee_key()` — returns `False` (allow) for employees, `True` (require) for SAGE direct
- Added `current_employee` context var (`app/tools/workspace.py`) set by Supervisor when spawning employee task

**Why:** The original design treated all `EXTERNAL_SIDE_EFFECT` tools as requiring approval. But employees already operate under hard budgets (iterations, tokens, wall-clock), post-hoc verification, and SAGE's `cancel_task` kill switch. Requiring approval per agent call created an "approval storm" that made autonomous work impossible. The distinction preserves SAGE's authority for direct invocations while letting employees work autonomously.

### D33 — Employee context propagation for correct agent session routing

**Decision:**
- Each employee's `run_opencode` / `run_hermes` calls now route to **that employee's own session DB** (`data/employees/<key>/sage.db`)
- Fixed hardcoded paths in `builtin.py` (was always `charlie` for OpenCode, `alpha` for Hermes)
- `AgentSessionManager` already tracked sessions per employee + backend + workspace — now correctly used

**Why:** Previously all employees shared Charlie's OpenCode sessions and Alpha's Hermes sessions. Now Charlie resumes Charlie's sessions, Alpha resumes Alpha's. Session continuity (`--continue`, `--session`) works per-employee per-workspace as designed.
