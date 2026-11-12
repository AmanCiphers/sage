# How SAGE Works

> **What this document contains:** A reference for the **running** SAGE system
> (not the target design). It covers the agentic loop and streaming, background
> employees and agent backends, verification, persistence/compaction, tools,
> memory and retrieval (FTS5 + embeddings + reranker), permissions and
> approvals, voice, the CLI/TUI, configuration, database tables, and a map of
> where each subsystem lives in `app/`. Design rationale is in
> [DECISIONS.md](DECISIONS.md); the long-term target is
> [ArchitectureV2.md](ArchitectureV2.md).

## The big picture

```
User message
   │
   ▼
ConversationLoop (app/conversation/loop.py)
   │  persist user turn to SQLite
   │  build history: system prompt + summary + recent tail
   ▼
┌──────────────────────────────────────────────┐
│ loop (max_iterations, default 8)             │
│                                              │
│  1. compact history if context is filling    │
│  2. call LLM (with tool definitions)         │
│  3. tool calls?                              │
│       YES → permission-check each call       │
│             execute (inline or background)   │
│             append tool results              │
│             go to step 1                     │
│       NO  → final answer, done               │
└──────────────────────────────────────────────┘
   │
   ▼
AssistantResponse (final answer is persisted too)
```

Everything is one SQLite database (`data/sage.db`), one process.
No queues, no external services.

## The agentic loop

`app/conversation/loop.py`. Instead of the old one-shot "decide then act"
flow, SAGE now runs a real tool loop:

1. The user turn is **persisted before the first LLM call** — a crash never
   loses an accepted request.
2. History is rebuilt each iteration from SQLite: system prompt + optional
   context summary + a token-budgeted tail of recent messages.
3. The LLM receives tool definitions in OpenAI function format
   (`ToolRegistry.openai_definitions()`).
4. If the model asks for tools, each call is:
   - parsed (JSON arguments),
   - looked up in the registry (unknown tool → error result back to the LLM),
   - checked by the **deterministic** `PermissionEngine` — the LLM can never
     approve its own actions,
   - executed by the `ExecutionEngine`,
   - the result (success or error) is appended as a tool message and the
     loop continues.
5. If the model returns plain text, that is the final answer.

Stop conditions: final answer, `max_iterations` (env `MAX_ITERATIONS`),
or a malformed tool-call batch (handled as an error result, loop continues).

### Streaming

Every LLM call streams (D20). `handle_stream(message, on_delta)` delivers
text deltas live (CLI prints them as they arrive — including tool-round
commentary like "Let me check the time..."); the loop accumulates the full
response and only then persists it, so the append-only history stays
exact. Tool-call fragments are assembled inside the provider and handed
to the loop complete. Provider fallback applies only before the first
delta — after the user has seen text, a failure raises rather than
re-emitting. Providers without SSE fall back to a single-delta stream.

A streaming turn can be **cancelled** from the Web UI: while SAGE is working,
the composer's send button becomes **STOP**, which aborts the browser fetch and
calls `POST /api/chat/stop`. The backend keeps a handle to the active turn task
and cancels it; the SSE stream closes with `{"stopped": true}` then
`{"done": true}`. Partial text already emitted stays in the feed; the turn is
not persisted as a completed assistant message.

## Staying free: background assignments

SAGE must never block the conversation. Two execution paths:

- **Inline** — small atomic tools (`get_current_time`, `task_status`, …)
  run inside the turn.
- **Background** — `assign_task(worker, objective)` creates a durable row
  in the `tasks` table and starts the named **employee** as a lazy asyncio
  task, returning a `task_id` **immediately**. The conversation is free at
  once. `task_status` reads durable state, so "how's Asta going?" is just
  a normal turn.

### Frozen contract (don't break SAGE)

SAGE's loop, tools, memory, scheduler, and UI touch only these entry
points. If they hold, the employee internals can change freely:

```text
assign_task(worker: str, objective: str) -> task_id
task_status(task_id: str) -> dict      # keys superset of the old shape
recover() -> int                       # startup reconciliation
```

`task_status` always returns at least:
`{task_id, objective, status, verification, verification_detail, result,
error}` — new fields (e.g. `worker`) are additive only.

### Employees: agent-workers, not subprocesses

`app/employees/supervisor.py` owns this. The old process-worker
(one-shot OS subprocess over a Unix socket, one tool per task, no mind)
was **removed** — each background unit of work is now an *employee*: a
named agent (roster: Asta, Otto, Tony, Alpha, Charlie) with its own
`ConversationLoop`, its own filtered tool registry, its own memory and
workspace (`data/employees/<key>/`), and a slim role card.

```text
SAGE --assign(worker, objective)--> Supervisor
                                       └─ durable tasks row (queued, worker=k)
                                          └─ lazy asyncio task: employee.work()
user <-------- SAGE narrates results <--- EventBus (employee_* / task_* events)
```

- **Permission checks** happen inside the employee's own loop on every
  tool call (same `PermissionEngine`), so backgrounding never bypasses
  consent. SAGE stays the approval authority: consent prompts created
  during an assignment route back through SAGE's normal approval flow.
- **Budgets** bound every assignment: `max_iterations`, `max_tokens`,
  `max_wall_seconds`, plus cancellation. By default **iterations, tokens,
  and wall-clock are all unlimited** (0 = unlimited). Tool calls are cheap;
  an employee may pursue an objective until done. SAGE intervenes via
  `cancel_task` if an assignment runs too long. Settings:
  `SAGE_EMPLOYEE_MAX_ITERATIONS`, `SAGE_EMPLOYEE_MAX_TOKENS`,
  `SAGE_EMPLOYEE_MAX_WALL_SECONDS` (all default 0).
- **Honest statuses** (ArchitectureV2 §12): `failed` = definitely failed;
  `unknown` = outcome cannot be determined (budget breach mid-action,
  cancel). On startup, leftover `queued`/`running` assignments from a
  previous SAGE run are marked `unknown` — their employee is gone.
- **Employee verification** is the employee's own evidence level, reported
  honestly and never upgraded by SAGE: `self-verified` = a side-effecting
  tool completed successfully (a real change was observed); `unverified` =
  the employee only read and reasoned, so its claim is its word. This is
  distinct from `unknown`, which means the outcome itself is indeterminate.
- **Employees never talk to the user**: they report to SAGE, which
  narrates. SAGE is the single point of contact.

### Agent backends: OpenCode & Hermes

Employees can invoke external agents for substantial work:

- **`run_opencode`** — For coding tasks (multi-file edits, refactoring, implementation).
  Uses OpenCode CLI: `opencode run --continue` (resume last), `--session <id>` (resume specific),
  `--model`, `--dir`. Employee-eligible, requires approval.
- **`run_hermes`** — For research, analysis, writing, general tasks.
  Uses Hermes CLI: `hermes chat -Q --format stream-json --continue` / `--resume <id>` / `--model` / `--in <dir>`.
  Employee-eligible, requires approval.

**Session continuity**: Each employee tracks agent sessions in `agent_sessions` table
(per-employee DB). New workspace → new session; same workspace → resume via stored
external session ID. This enables "continue where you left off" across assignments.

**Cancellation**: SAGE can terminate any running task via `cancel_task` (SAGE-only tool).
No hard wall-clock timeout — SAGE decides when to intervene (OpenCode can take 20-30 min).

**Streaming**: both backends drain stdout and stderr **concurrently** and read
lines through `iter_output_lines()` (`app/agents/interface.py`), which splits
fixed chunks on newlines. A child that floods stderr cannot deadlock the turn,
and a single line above asyncio's 64 KiB reader limit does not abort the
stream. On non-zero exit the trailing stderr is logged.

### Verification: results are re-checked, not trusted

The executor's own claim ("done") is never trusted (ArchitectureV2 §60–62).
After a background task or approved operation *succeeds*,
`app/execution/verification.py` re-checks the real world state:

| Tool | Checks |
|---|---|
| `write_file` | file exists AND content == requested content |
| `patch` | file exists, old text absent, new text present |
| `run_command` | exit code 0, no error/timeout payload |
| anything else | `unverified` — reported honestly, never faked as passed |

The outcome is stored per task (`verification`, `verification_detail`) and
shown by `task_status`. A `failed` verification marks the task `failed`
(even though the tool "succeeded"); approved operations become `FAILED`
the same way.

An **LLM verifier** rung also exists in `app/execution/verification.py`
(`_llm_verify`) for outcomes the deterministic checks cannot judge. It is
opt-in via `SAGE_FEATURE_LLM_VERIFIER=1` and defaults to off, so the
deterministic checks remain the baseline (D23).

## The forever chat: persistence and compaction

SAGE has one continuous conversation. Raw history is permanent
(ArchitectureV2 §30): `messages` is append-only, and compaction only changes
what the LLM *sees*, never what is stored.

### What the LLM sees each turn

`app/context/builder.py` assembles:

```
[system prompt] + [live date/time section] + [summary row (if any)] + [tail: recent messages up to a token budget]
```

The **live date/time section** is rebuilt on every turn (not once at startup)
from `settings.timezone` (default `Asia/Kolkata`): weekday, date, time, UTC
offset, and the IANA zone name. This gives the agent a passive sense of "now"
for relative requests ("today", "tomorrow", "this evening") without relying on
a tool call. `get_current_time` / `get_current_date` use the same timezone for
explicit queries. Employee workers use the same `ContextBuilder`, so they see
the clock too.

### When compaction happens

`app/context/compressor.py`:

- **Trigger**: estimated prompt tokens ≥ `context_window × 0.5`.
- **Anti-thrash**: if two compactions in a row fail to get the prompt under
  the threshold ("ineffective strikes"), further compaction is blocked until
  an effective one happens. This prevents expensive compaction loops.
- **Windowing**: tail = newest messages up to ~2.5% of the window
  (bounded 2000–8000 tokens). An assistant tool-call message is never
  separated from its tool results.
- **Summarization**: the middle (everything between the summary row and the
  tail) is merged with the *previous* summary into a new summary by **one**
  LLM call with a structured prompt (goal, active state, decisions, errors,
  files). The previous summary is never re-summarized from scratch — it is
  iteratively extended.
- **Deterministic fallback**: if the summarizer LLM fails, a static summary
  is built from the recent user messages (and the previous summary). History
  is never lost to a summarizer outage.
- The summary is stored in `session_state`, not in `messages`, and injected
  as a clearly-marked user row ("background context, not new user input").

### Crash resilience

- user turn persisted before the first LLM call,
- tool-call assistant message persisted **before** tools execute,
- tool results persisted as they complete,
- final answer persisted.
So after a restart, the conversation can resume from the database.

## Tools

- Defined in `app/tools/models.py` (`ToolDefinition`: name, description,
  pydantic input schema, classification, destructive flag, handler).
- Registered into `ToolRegistry` (`app/tools/registry.py`).
- **Path resolution:** relative paths in file tools anchor to the
  workspace directory (`SAGE_WORKSPACE_PATH`, default `data/workspace`) —
  never the process cwd. `~` expands; missing parent dirs are created
  on write (D19).
- Builtin tools (grouped):
  - **Facts:** `get_current_time`, `get_current_date`
  - **Files:** `read_file`, `list_files`, `write_file`, `patch`
  - **Shell:** `run_command` (risk-classified; see Permissions)
  - **Workspace:** `authorize_workspace`
  - **Investigation:** `grep`, `glob` (ripgrep-backed), `web_search`
    (Tavily if `SAGE_TAVILY_API_KEY` is set, else Bing RSS), `browse_url`
  - **Memory / recall:** `memory`, `session_search`
  - **Skills:** `skill_manage`, `skill_view`, `skill_index`
  - **Background work:** `assign_task`, `task_status`, `await_task`,
    `cancel_task` (SAGE-only)
  - **Agent backends:** `run_opencode` (coding), `run_hermes` (research)
  - **Voice:** `speak` (TTS; one call per turn)
  - **Notifications:** `send_notification`
  - **Scheduler / proactive:** `schedule_job`, `list_jobs`, `enable_job`,
    `remove_job`, `list_rules`, `add_proactive_rule`, `enable_rule`,
    `remove_rule`
- Handlers may be sync or async; the `ExecutionEngine` awaits async handlers.

## Long-term memory

`app/memory/store.py` — the default: a bounded markdown file
(`data/memory.md`, default 4000 chars) of durable facts: preferences,
projects, goals, decisions. Not the whole chat (raw history covers that) —
the short list worth keeping in every prompt after compaction.

- The agent edits it explicitly via the `memory` tool (no automatic
  extraction yet).
- `ContextBuilder` injects it into the system prompt each turn
  ("Known facts about the user"), so facts survive even when the
  conversation rows that mentioned them have left the tail window.

**Memory v2 (opt-in).** `app/memory/store_v2.py` adds per-fact provenance
(`source_msg_id`, confidence, explicit/inferred), lifecycle status
(`active` / `superseded` / `forgotten`), `supersede`, and `forget`, backed by
`memory_v2.json`. It is selected only when `SAGE_FEATURE_MEMORY_V2=1`
(`app/tools/memory_tools.py`), and `session_search` includes those memories
when the flag is on.

## Session search (hybrid retrieval)

`messages_fts` is a SQLite FTS5 index over the permanent raw history, kept
in sync on every append and backfilled once for pre-existing rows. Alongside
it, `message_embeddings` stores vectors and `ContextBuilder` retrieves a
hybrid result set (FTS5 + embeddings, 40/60 by default) that is injected into
the prompt each turn. `session_search` exposes the same history to the model,
including what compaction removed from context, and can also search skills
and v2 memories.

## Permissions

`app/permissions/risk.py` classifies each invocation deterministically (no
LLM), and `app/permissions/engine.py` turns that into a decision:

| Risk | Meaning | Decision |
|---|---|---|
| `allow` | ordinary read, normal shell command, ordinary file edit | ALLOWED (silent) |
| `review` | ambiguous shell construct (`; \| & $() xargs eval bash -c`, heredoc, redirect) | smart guardian; allow or ask |
| `ask` | known-dangerous (`sudo`, recursive `rm`, credentials, download \| interpreter) | APPROVAL_REQUIRED |
| `deny` | catastrophic (`rm -rf /`, `mkfs`, raw-device write, shutdown) | DENIED — never bypassable |

Order matters: `run_command` and file writes are classified **before** any
bypass mode (YOLO, off, approving contexts), so YOLO can never approve a
hardline command. The smart guardian may only refine `review`; it cannot
downgrade `ask` or `deny`.

File writes are silent for ordinary files and files inside the current or an
explicitly authorized workspace. Sensitive files (`.env`, keys/certs, shell
rc files, `.ssh`/`.aws`, databases, system paths) written outside a trusted
directory ask. Build/dependency files are not considered sensitive.
`full_machine_access` (default true) allows the rest of the Mac.

**Secret *reads* are detected by argument, not by reader** (D48). A command
is tokenized, `$HOME` expanded, and every argument checked against the secret
file/directory sets — so `less ~/.ssh/id_rsa`, `grep SECRET .env`,
`base64 ~/.ssh/id_rsa`, and `tar czf x.tar ~/.ssh` all ask, not just a fixed
list of verbs. Listings and navigation (`ls`, `cd`, `find ~/.ssh`) do not ask,
and templates (`.env.example`, `.sample`, `.template`, `.dist`) are not secret.

**Capability routing normalizes the command name.** `run_command` resolves
capabilities from the command's basename, so `/usr/bin/curl` and `./curl`
classify identically to `curl` (`NETWORK_COMMANDS` / `SYSTEM_COMMANDS` in
`app/tools/shell_parser.py`). A directory prefix can never move a command into
a different capability class.

The old per-tool `requires_approval` hooks were removed; the loop's single
`await permission_engine.check_async(tool, arguments)` is the enforcement
point. Non-risk tools still fall back to capability policies and the legacy
classification table:

| Classification | Decision |
|---|---|
| read_only | ALLOWED |
| local_mutation, non-destructive | ALLOWED |
| local_mutation, destructive | APPROVAL_REQUIRED |
| external_side_effect | APPROVAL_REQUIRED |
| unknown | DENIED |

`assign_task` remains structurally validated: the worker must exist, budgets
are hard, and every employee tool call is permission-checked inside its own
loop — backgrounding never bypasses consent (D23/D24).

## Approvals and operations

When a tool call needs consent:

1. The loop records a pending row in `approvals` — bound to the exact
   tool name + arguments + resource, expiring in 10 minutes. Nothing runs.
2. The tool result tells the model "approval required"; the model explains
   the action to the user and the turn ends with
   `AssistantResponse.pending_approval` set.
3. The caller (CLI prompt, Web UI) asks the user. On **approve**,
   `SAGEApplication.approve_pending` executes *exactly the bound
   arguments* (never new input) through the **Operation Manager** —
   approval is bound data, not a boolean (ArchitectureV2 §27). The approval
   carries a scope:
   - `session` (default, Web UI sends `?scope=session`): remember the
     tool + normalized resource (`run_command:<cmd>`,
     `write_file:<abs path>`) for the rest of the session, so the same
     action does not prompt again. A different command/ path still does.
   - `permanent`: persist in the permanent allowlist.
   After executing, the model is told to **continue the task** and stop at
   the next approval, so a multi-step job proceeds one approval at a time.
   The Web UI re-shows the next approval banner automatically if the
   continuation produces one.
4. On **deny**, the action never runs; the denial is fed back into a normal
   turn.

Approval prompts always show the concrete action being approved: the
normalized command for `run_command`, the path for `write_file`/`patch`, and
the objective for the agent tools.

Hardline (`deny`) results never create an approval row in the first place,
so there is nothing to approve; they are refused outright.

Every approved (and crash-trackable) side effect gets an **operation**
row: `EXECUTING → SUCCEEDED | FAILED`, recorded *before* execution so a
crash never loses track. On startup, anything still `EXECUTING` is marked
`UNKNOWN` — the outcome cannot be safely determined (ArchitectureV2 §12).

Code: `app/permissions/approvals.py`, `app/operations/manager.py`.

## Voice

Voice is another client of SAGE — a voice turn is a normal `UserMessage`
through the same conversation engine. Enabled with `SAGE_FEATURE_VOICE=1`;
see [`docs/voice.md`](voice.md).

Pipeline: browser mic (16 kHz PCM) → `/ws/voice` (transcription only) → VAD →
faster-whisper → transcript inserted into chat → browser calls
`POST /api/chat/stream` with `voice: true` → streamed response in chat + a
concise **conversational line** via the `speak` tool → TTS (Chatterbox primary,
Piper fallback) → WAV → `/ws/audio` → browser speaker. Long speech is split
into sentence-aware chunks, requested with bounded prefetch, and broadcast as
each chunk is synthesized; the browser plays them sequentially from a FIFO
queue. Each WAV is preceded by an `audio` metadata frame and the browser
acknowledges playback (`audio_played` / `audio_failed`), so `speak` can report
`heard`, `delivered`, and `chunks` instead of assuming success. The
`voice_status` tool exposes the connected-client count and last playback error.
The voice turn carries a per-turn instruction requiring `speak`; if the model
omits it, the backend synthesizes the final text as a fallback.

Interaction: **push-to-talk** (default, hold to talk) or **wake word**
(opt-in). Resource-light on Apple Silicon: no torch, models load lazily and
only during a turn, Chatterbox TTS is a network call.

## The CLI and TUI

On an interactive TTY, `main.py` launches the **Textual TUI**
(`app/cli_tui.py`): a full-screen conversation feed with Markdown responses,
a persistent input box (↑/↓ history), a toggleable status side panel (Tab),
and a modal approval dialog (`y` approve / `n`/`esc` deny). It drives the same
`ConversationLoop` through the callback contract (`on_delta`, `on_event`).

When stdout is not a TTY (pipes, CI, `NO_COLOR`), it falls back to the ANSI
renderer in `app/cli.py` — a thin view over the loop's `on_event` hook (D21):

- **Spinner** while the model thinks; stops before the first token.
- **Tool rounds**: `⚙ read_file (path=…)` then `✓ 12ms` or `✗ error`.
- **Approvals**: marked inline, then a styled `Approve …? [y/N]` prompt.
- **Stats footer** per turn: `(1.4s · 2 tool calls)`.

Slash commands available to both paths: `/help`, `/tools`, `/memory`,
`/search <q>`, `/jobs`, `/rules`, `/yolo`, `/approvals`, `/exit`, `/quit`.
Colors/spinner auto-disable when stdout is not a TTY or `NO_COLOR` is set.

Logs (INFO+) go to `data/sage.log`; the console only shows warnings and
errors, so streaming output and the spinner stay uncluttered.

## Configuration (`.env`)

All settings use the `SAGE_` env prefix (`app/config.py`, `env_prefix="SAGE_"`).

| Setting | Default | Meaning |
|---|---|---|
| `SAGE_MAX_ITERATIONS` | 50 | Loop iteration cap per turn (SAGE's own turn) |
| `SAGE_EMPLOYEE_MAX_ITERATIONS` | 0 (unlimited) | Employee assignment iteration cap |
| `SAGE_EMPLOYEE_MAX_TOKENS` | 0 (unlimited) | Employee assignment token cap |
| `SAGE_EMPLOYEE_MAX_WALL_SECONDS` | 0 (unlimited) | Employee assignment wall-clock cap (no default runaway guard) |
| `SAGE_CONTEXT_WINDOW_TOKENS` | 32000 | Assumed context window for compaction |
| `SAGE_COMPACTION_THRESHOLD_RATIO` | 0.5 | Compact when prompt reaches this fraction |
| `SAGE_HISTORY_FETCH_LIMIT` | 400 | Max raw messages fetched per turn |
| `SAGE_MAX_TOOL_RESULT_CHARS` | 8000 | Truncation cap for tool output fed to the LLM |
| `SAGE_WORKSPACE_PATH` | `data/workspace` | Root for relative file-tool paths |
| `SAGE_TOOLSET_PROFILE` | `full` | Active toolset profile |
| `SAGE_OPENCODE_PATH` | `opencode` | Path to OpenCode CLI |
| `SAGE_HERMES_PATH` | `hermes` | Path to Hermes CLI |
| `SAGE_OPENCODE_DEFAULT_MODEL` | `nebius/deepseek-ai/DeepSeek-V4-Flash-0731` | Default model for OpenCode |
| `SAGE_HERMES_DEFAULT_MODEL` | `openai/gpt-4` | Default model for Hermes |
| `SAGE_RAG_ENABLED` | `true` | Enable hybrid retrieval |
| `SAGE_RAG_FTS_WEIGHT` / `SAGE_RAG_EMBEDDING_WEIGHT` | `0.4` / `0.6` | Hybrid merge weights |
| `SAGE_RAG_RERANK_ENABLED` | `false` | Optional cross-encoder reranker |
| `SAGE_FEATURE_REAL_TOKENIZER` | `false` | Use Qwen tokenizer for token counts |
| `SAGE_FEATURE_THREAT_SCAN` | `false` | Scan context files for injection patterns |
| `SAGE_FEATURE_MEMORY_V2` | `false` | Enable memory v2 (provenance/lifecycle) |
| `SAGE_FEATURE_LLM_VERIFIER` | `false` | Enable the LLM verification rung |
| `SAGE_FEATURE_VOICE` | `false` | Enable `/ws/voice` and the Web voice bar |

> **Note:** `SAGE_FEATURE_PARALLEL_TOOLS`, `SAGE_FEATURE_SKILLS`, and
> `SAGE_FEATURE_COMPACTION_V2` are currently **defined but not enforced** —
> skills are always registered and compaction v2 is always used. Treat them
> as reserved flags rather than toggles.

## Database tables

| Table | Purpose |
|---|---|
| `messages` | Append-only raw conversation history |
| `messages_fts` | FTS5 full-text index over raw history |
| `session_state` | Summary text + compaction guards (strikes, counts) |
| `tasks` | Durable background task records (status, result, error) |
| `approvals` | Bound, expiring user consent for side-effecting actions |
| `operations` | Side-effect execution log (EXECUTING/SUCCEEDED/FAILED/UNKNOWN) |
| `agent_sessions` | Employee agent backend sessions (opencode/hermes) |

Plus `data/memory.md` — the bounded long-term memory file.

## Where things live

```
app/
├── application.py          # wiring / composition root
├── cli.py / cli_tui.py     # ANSI fallback renderer / Textual TUI
├── config.py               # Settings (SAGE_ env prefix)
├── feature_flags.py        # feature-flag accessors
├── agents/                 # external agent backends (opencode, hermes)
│   ├── interface.py        # AgentBackend abstract base
│   ├── opencode.py         # OpenCode CLI wrapper
│   ├── hermes.py           # Hermes CLI wrapper
│   ├── factory.py          # backend creation
│   └── sessions.py         # session tracking (AgentSessionManager)
├── conversation/
│   ├── loop.py             # the agentic loop
│   ├── system_prompt.py    # SOUL/authority/tools/memory assembly + threat scan
│   └── models.py           # UserMessage / AssistantResponse
├── context/
│   ├── builder.py          # history assembly + hybrid RAG injection
│   ├── compressor.py       # compaction algorithm
│   └── tokens.py           # real tokenizer (flag) or chars/4 fallback
├── decision/               # personality / tone guidance
├── embeddings/
│   ├── provider.py         # embedding API client
│   ├── store.py            # message_embeddings persistence + cosine search
│   └── indexer.py          # backfill
├── employees/
│   ├── supervisor.py       # assign / get_status / recover (frozen contract)
│   ├── employee.py         # one employee's agent loop under budgets
│   ├── roster.py           # fixed roster of five + name normalization
│   ├── factory.py          # per-employee DB, memory, workspace, tools
│   ├── budgets.py          # iterations / tokens / wall-clock ceilings
│   └── roles/              # slim role cards (asta.md, …)
├── events/                 # EventBus
├── execution/
│   ├── engine.py           # inline tool execution
│   ├── verification.py     # deterministic + optional LLM outcome checks
│   └── models.py
├── inference/              # providers, router, fallback, manager
├── interfaces/             # Protocol definitions
├── memory/
│   ├── store.py            # bounded v1 long-term memory (memory.md)
│   └── store_v2.py         # v2 provenance / supersede / forget (flag)
├── notifications/          # in-chat completion notices + OS notifications
├── observability/          # logging helpers
├── operations/
│   └── manager.py          # tracked side effects (op IDs, crash recovery)
├── persistence/
│   ├── schema.py           # DDL + FTS5 index/backfill
│   ├── migrations.py       # additive migration framework
│   ├── store.py            # MessageStore / SessionStateStore / TaskStore
│   └── database.py
├── permissions/
│   ├── engine.py           # decision enforcement
│   ├── risk.py             # deterministic allow/review/ask/deny classifier
│   ├── smart.py            # smart guardian (review only)
│   ├── approval.py         # hardline patterns
│   ├── approvals.py        # bound, expiring user consent
│   ├── allowlist.py        # persistent + session approval memory
│   ├── command_policy.py / detection.py / gate.py / policy.py
│   ├── config.py / context.py / models.py / utils.py
├── recovery/               # startup reconciliation
├── reranker/               # optional cross-encoder reranker
├── response/               # response shaping
├── scheduler/              # timezone-aware cron + run_at jobs
├── skills/
│   ├── store.py            # JSONL skill persistence + versioning
│   └── tools.py            # skill_manage / skill_view / skill_index
├── tools/
│   ├── registry.py / models.py / schemas.py / toolsets.py
│   ├── builtin.py          # task/scheduler/proactive/agent/notification tools
│   ├── file_tools.py / shell_tools.py / shell_parser.py / workspace.py
│   ├── investigation.py    # grep / glob / web_search / browse_url
│   ├── memory_tools.py / session_search.py
│   ├── threat_patterns.py  # context-file injection scanning
│   └── speak_tool.py       # voice output
├── voice/
│   ├── config.py / interfaces.py
│   ├── audio.py / audio_bus.py / vad.py / asr.py / tts.py / wakeword.py
│   ├── session.py          # ASR -> transcript
│   └── gateway.py          # /ws/voice
└── workers/                # empty — process-worker removed in favour of employees (D28)
```

`app/verification/` is an empty placeholder; verification actually lives in
`app/execution/verification.py`.
