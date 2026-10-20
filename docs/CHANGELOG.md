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

---

# Part B — Proposed: Employee Workers

**Status: proposed, not built.** Captured here so the direction is not lost.
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
