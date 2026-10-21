# How SAGE Works

This explains the running system after the agentic-loop rework.
Design rationale lives in [DECISIONS.md](DECISIONS.md).
The full target architecture is in [../ArchitectureV2.md](../ArchitectureV2.md).

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
  `max_wall_seconds`, plus cancellation. By default **iterations and tokens
  are unlimited** (tool calls are cheap; an employee may pursue an objective
  until done) and only **wall-clock** guards against a runaway. Any ceiling
  set to `0`/`None` means unlimited. A breach ends the assignment honestly as
  `failed` (or `unknown` when the outcome is indeterminate). Settings:
  `SAGE_EMPLOYEE_MAX_ITERATIONS`, `SAGE_EMPLOYEE_MAX_TOKENS`,
  `SAGE_EMPLOYEE_MAX_WALL_SECONDS`.
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
the same way. No LLM verifier yet — deterministic checks cover every tool
we run today (D23).

## The forever chat: persistence and compaction

SAGE has one continuous conversation. Raw history is permanent
(ArchitectureV2 §30): `messages` is append-only, and compaction only changes
what the LLM *sees*, never what is stored.

### What the LLM sees each turn

`app/context/builder.py` assembles:

```
[system prompt] + [summary row (if any)] + [tail: recent messages up to a token budget]
```

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
  workspace directory (`SAGE_WORKSPACE`, default `data/workspace`) —
  never the process cwd. `~` expands; missing parent dirs are created
  on write (D19).
- Builtin tools:
  - `get_current_time`, `get_current_date` — quick facts
  - `read_file`, `list_files` — read-only local file access
  - `write_file` — create/edit files (always immediate; edits never
    require approval — A5)
  - `patch` — surgical exact-text replacement, same policy as `write_file`
  - `run_command` — shell in the workspace cwd, timeout + output caps;
    read-only commands run inline, everything else needs approval
  - `memory` — manage long-term memory (add/remove/list/clear)
  - `session_search` — full-text search over the entire conversation history
  - `assign_task`, `task_status` — background execution: hand an
    objective to a named employee (see "Staying free")
- Handlers may be sync or async; the `ExecutionEngine` awaits async handlers.

## Long-term memory

`app/memory/store.py` — a bounded markdown file (`data/memory.md`,
default 4000 chars) of durable facts: preferences, projects, goals,
decisions. Not the whole chat (raw history covers that) — the short list
worth keeping in every prompt after compaction.

- The agent edits it explicitly via the `memory` tool (no automatic
  extraction yet).
- `ContextBuilder` injects it into the system prompt each turn
  ("Known facts about the user"), so facts survive even when the
  conversation rows that mentioned them have left the tail window.

## Session search (FTS5)

`messages_fts` is a SQLite FTS5 index over the permanent raw history,
kept in sync on every append and backfilled once for pre-existing rows.
Exposed to the model as `session_search` — it can recall anything ever
said, including what compaction removed from context.

## Permissions

`app/permissions/engine.py` + `policy.py` — deterministic, no LLM:

| Classification | Decision |
|---|---|
| read_only | ALLOWED |
| local_mutation, non-destructive | ALLOWED |
| local_mutation, destructive | APPROVAL_REQUIRED |
| external_side_effect | APPROVAL_REQUIRED |
| unknown | DENIED |

Some tools also have a **dynamic** hook (`ToolDefinition.requires_approval`):
given the actual arguments, SAGE decides if *this particular invocation*
needs consent. Deterministic, never the LLM's call:

- `write_file`/`patch` — never needs approval (A5): edits are a
  first-class local mutation; the engine only backstops the hard-block
  list (D28).
- `run_command` — must pass the command classifier
  (`app/permissions/command_policy.py`): first token on the read-only
  allowlist AND no shell metacharacters (`; | & < > \` $()`). `ls; rm -rf /`
  is blocked by the metacharacter rule alone. Everything else → approval.
- `assign_task` — validation is structural: the worker must exist on the
  roster, and the assignment runs under hard budgets (iterations, tokens,
  wall-clock). Every tool call the employee makes is permission-checked
  inside its own loop — backgrounding never bypasses consent (D23/D24).

## Approvals and operations

When a tool call needs consent:

1. The loop records a pending row in `approvals` — bound to the exact
   tool name + arguments + resource, expiring in 10 minutes. Nothing runs.
2. The tool result tells the model "approval required"; the model explains
   the action to the user and the turn ends with
   `AssistantResponse.pending_approval` set.
3. The caller (CLI prompt, future Web UI) asks the user. On **approve**,
   `SAGEApplication.approve_pending` executes *exactly the bound
   arguments* (never new input) through the **Operation Manager** —
   approval is bound data, not a boolean (ArchitectureV2 §27).
4. On **deny**, the action never runs; the denial is fed back into a normal
   turn.

Every approved (and crash-trackable) side effect gets an **operation**
row: `EXECUTING → SUCCEEDED | FAILED`, recorded *before* execution so a
crash never loses track. On startup, anything still `EXECUTING` is marked
`UNKNOWN` — the outcome cannot be safely determined (ArchitectureV2 §12).

Code: `app/permissions/approvals.py`, `app/operations/manager.py`.

## The CLI

`main.py` + `app/cli.py` — a thin view over the loop's `on_event` hook
(D21):

- **Spinner** while the model thinks; stops before the first token.
- **Tool rounds**: `⚙ read_file (path=…)` then `✓ 12ms` or `✗ error`.
- **Approvals**: marked inline, then a styled `Approve …? [y/N]` prompt.
- **Stats footer** per turn: `(1.4s · 2 tool calls)`.
- **Slash commands**: `/help`, `/tools`, `/memory`, `/search <q>`, `/exit`.
- Colors/spinner auto-disable when stdout is not a TTY or `NO_COLOR` is set.

Logs (INFO+) go to `data/sage.log`; the console only shows warnings and
errors, so streaming output and the spinner stay uncluttered.

## Configuration (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `MAX_ITERATIONS` | 8 | Loop iteration cap per turn (SAGE's own turn) |
| `EMPLOYEE_MAX_ITERATIONS` | 0 (unlimited) | Employee assignment iteration cap |
| `EMPLOYEE_MAX_TOKENS` | 0 (unlimited) | Employee assignment token cap |
| `EMPLOYEE_MAX_WALL_SECONDS` | 600 | Employee assignment wall-clock cap (the runaway guard) |
| `CONTEXT_WINDOW_TOKENS` | 32000 | Assumed context window for compaction |
| `COMPACTION_THRESHOLD_RATIO` | 0.5 | Compact when prompt reaches this fraction |
| `HISTORY_FETCH_LIMIT` | 400 | Max raw messages fetched per turn |
| `MAX_TOOL_RESULT_CHARS` | 8000 | Truncation cap for tool output fed to the LLM |
| `SAGE_WORKSPACE` | `data/workspace` | Root for relative file-tool paths |

## Database tables

| Table | Purpose |
|---|---|
| `messages` | Append-only raw conversation history |
| `messages_fts` | FTS5 full-text index over raw history |
| `session_state` | Summary text + compaction guards (strikes, counts) |
| `tasks` | Durable background task records (status, result, error) |
| `approvals` | Bound, expiring user consent for side-effecting actions |
| `operations` | Side-effect execution log (EXECUTING/SUCCEEDED/FAILED/UNKNOWN) |

Plus `data/memory.md` — the bounded long-term memory file.

## Where things live

```
app/
├── application.py          # wiring / composition root
├── conversation/
│   ├── loop.py             # the agentic loop
│   ├── system_prompt.py
│   └── models.py           # UserMessage / AssistantResponse
├── context/
│   ├── builder.py          # history assembly (system + summary + tail)
│   ├── compressor.py       # compaction algorithm
│   └── tokens.py           # cheap token estimation
├── employees/
│   ├── supervisor.py       # assign / get_status / recover (frozen contract)
│   ├── employee.py         # one employee's agent loop under budgets
│   ├── roster.py           # fixed roster of five + name normalization
│   ├── factory.py          # per-employee DB, memory, workspace, tools
│   ├── budgets.py          # iterations / tokens / wall-clock ceilings
│   └── roles/              # slim role cards (asta.md, …)
├── execution/
│   ├── engine.py           # inline tool execution
│   ├── verification.py     # SAGE-side outcome re-checks
│   └── models.py
├── inference/              # providers, router, fallback, manager
├── memory/
│   └── store.py            # bounded long-term memory (memory.md)
├── operations/
│   └── manager.py          # tracked side effects (op IDs, crash recovery)
├── persistence/
│   ├── schema.py           # DDL + FTS5 index/backfill
│   ├── store.py            # MessageStore (w/ search) / SessionStateStore / TaskStore
│   └── database.py
├── permissions/
│   ├── engine.py           # deterministic permission engine + policy
│   └── approvals.py        # bound, expiring user consent
└── tools/                  # registry + builtin tools (files, memory, search, tasks)
```
