# Plan — Replace Process-Worker with Worker-Employee System

**Status:** approved direction, ready to implement (build mode).
**Companion docs:** [CHANGELOG.md](CHANGELOG.md) · [DECISIONS.md](DECISIONS.md) ·
[HOW_IT_WORKS.md](HOW_IT_WORKS.md) · [../ArchitectureV2.md](../ArchitectureV2.md)

---

## 1. Context & decisions locked

The current background system is a **one-shot OS subprocess** (`app/execution/worker.py`
+ `ProcessExecutor` + a Unix-socket protocol) that runs exactly one tool and returns a
raw result — it has **no mind**. We are **removing it entirely** and replacing it with a
**Worker-Employee** system in which each worker is an *agent* that reports to SAGE.

Decisions confirmed with the user:

| # | Decision |
|---|---|
| N1 | Today's "worker" is conceptually a **Process**. The whole worker/process system is **removed** — fresh build, no dead code kept. |
| N2 | SAGE dispatches **goals to named employees**, not tools. `start_task` → **`assign_task(worker, objective)`**. |
| N3 | Employees are **identity-persistent, process-lazy**: the roster is permanent; an employee's loop runs only when assigned work, then idles (standby without idle burn). |
| N4 | Each employee has its **own memory** (past conversations/work), so it remembers prior assignments. |
| N5 | **SAGE is the permission authority.** The `PermissionEngine` becomes a **final safety guard** with a small **immutable hard-block list** SAGE cannot override. |
| N6 | **No cap** on simultaneous active employees. |
| N7 | Full replacement of the process system. |
| N8 | Per-assignment **hard limits** (iterations, tokens, wall-clock, kill switch) are mandatory. |
| N9 | **One employee first** (`asta`); roster of 5 — Asta, Otto, Tony, Alpha, Charlie — added later. |
| N10 | UI is later, but everything is built **API-first** so REST/WS attachment is trivial. |

### Naming & identity (resolved)
- **Roster (5, fixed):** Asta, Otto, Tony, Alpha, Charlie.
- **Core logic:** lowercase ids — `asta, otto, tony, alpha, charlie`.
- **id structure:** `employee-asta`; **name** field (display): `Asta`.
- **UI/CLI:** cased display names ("Asta has finished, sir").
- **Casing:** matching is case-insensitive; internal id is the stable key.

### Philosophy change note (N5) — to be recorded as D28
The original principle (D1/D22) was *"the LLM never declares an action safe."* We are
moving to **SAGE judges + engine backstop**. Rationale: speed, and it is the only model
that scales to N autonomous employees without an approval storm. **Guardrails that keep
this honest:** (a) the immutable hard-block list catches the catastrophic class no LLM can
override; (b) SAGE-side **post-hoc verification** of real outcomes stays; (c) every SAGE
permission decision is **logged** for audit. **Timing:** land D28 as its own documented
step *after* the worker replacement compiles and passes — one big change at a time.

---

## 2. The frozen contract ("don't break SAGE")

SAGE's loop, tools, memory, scheduler, and UI touch only these entry points. If they hold,
the replacement cannot break SAGE.

```
assign_task(worker: str, objective: str) -> task_id     # was start_task(tool, args)
task_status(task_id: str) -> dict                       # shape unchanged
recover() -> int                                        # startup reconciliation
```

`task_status` returns **at least** today's keys so `web/Console.tsx` and `/api/tasks`
keep working:

```json
{ "task_id": str, "objective": str, "status": str,
  "verification": str|null, "verification_detail": str|null,
  "result": str|null, "error": str|null }
```

New fields (e.g. `worker`, `progress`) are **additive only**.

---

## 3. Architecture

```
User ── chat ──> SAGE (ConversationLoop)
                  │  assign_task(worker, objective)
                  ▼
              Supervisor ──── owns ────> TaskStore (durable rows)
                  │  routes
                  ▼
          ┌──────────────────────────────────────────┐
          │ Employee  (roster: alpha, …)             │
          │  own ConversationLoop + InferenceManager │
          │  own ToolRegistry (filtered subset)       │
          │  own MemoryStore                          │
          │  role card (slim mandate)                 │
          │  budgets: iters / tokens / wall-clock     │
          └──────────────────────────────────────────┘
                  │  per-step events
                  ▼
          Supervisor narrates status to SAGE
                  │
          EventBus ──> WS/REST (UI, later)
```

- Employees run as **lazy asyncio tasks inside SAGE's process** (N3 default). Heavy tool
  work inside an employee still cannot stall chat because it is awaited off the turn.
  *(Open: if hard OS isolation is later required, swap the executor seam — see §8.)*
- SAGE is the **only** thing the user talks to. Employees never address the user.

---

## 4. Execution order (each step compiles + tests green before the next)

### Phase 0 — Contract freeze (docs only)
- Add this contract to `HOW_IT_WORKS.md` and note N1 in `DECISIONS.md` (as D28 pending).
- **No code.**

### Phase 1 — Excise the old system
- **Delete:** `app/execution/worker.py`, `app/execution/executor.py`
  (`ProcessExecutor`, `InlineExecutor`, UDS dance).
- **Keep:** `app/execution/verification.py` (SAGE-side verify stays), `models.py`
  (`ToolCall`).
- **Protocol:** `app/execution/protocol.py` — remove `worker.py`'s consumer.
  Decide: delete if in-process employees need no wire (likely), or keep for a future
  isolated executor. *(Recommend: delete; reintroduce behind the seam if needed.)*
- Update `app/application.py` imports (remove `TaskDispatcher`/`ProcessExecutor`).
- Update tests referencing the removed classes → temporary skip until Phase 7.
- **Gate:** `python -c "import app.application"` + `ruff check app`.

### Phase 2 — Employee core (one employee)
New package `app/employees/`:
- `employee.py` — `Employee`: name, role card, own `ConversationLoop`,
  `InferenceManager`, `ToolRegistry` (filtered subset), `EmployeeMemory`, budgets.
  `async def work(objective) -> EmployeeResult`; emits per-step events via a callback.
- `roles/alpha.md` — slim role card (mandate, allowed tools, "return a concise result").
- `memory.py` — `EmployeeMemory` (own store; N4). Start as separate SQLite file/table
  keyed by employee name.
- `budgets.py` — `Budget` dataclass (max_iterations, max_tokens, max_wall_seconds) +
  enforcement wrapper that raises `BudgetExceeded`.
- `roster.py` — fixed registry; register **one** employee now (`alpha`), schema ready
  for five (N9).

### Phase 3 — Supervisor (the SAGE-facing seam)
New `app/employees/supervisor.py`:
- `Supervisor` replaces `TaskDispatcher`. Same three methods (`assign`, `get_status`,
  `recover`) with the frozen signatures (§2).
- On `assign(worker, objective)`: create durable task row (with `worker`), spawn a lazy
  asyncio task running the employee under budget enforcement, return `task_id`.
- On completion/failure/budget-breach: write `set_status(...)` honestly
  (`completed` / `failed` / `unknown` per the existing honesty rule).
- SAGE-side permission check **before** assignment and **verification after** (kept).
- Publish `task_*`/`worker_*` events to `EventBus` (also feeds the deferred UI panel).

### Phase 4 — Wire into SAGE (minimal edits)
- `app/application.py`: build `Supervisor` instead of `TaskDispatcher`
  (`application.py:97-103`); update `_wire_tool_handlers` (`application.py:182-209`).
- `app/tools/builtin.py`:
  - Replace the `start_task` tool with **`assign_task(worker, objective)`**
    (schema `AssignTaskInput`), handler → `supervisor.assign`. (`builtin.py:212-266`)
  - Keep `task_status` (unchanged shape).
  - Decide `worker_eligible` field: **remove** (N2) — or rename to `employee_eligible`
    if employees still need a tool filter. *(Recommend: repurpose as
    `employee_eligible` for the per-employee tool filter.)*
- `app/conversation/tools.md`, `app/conversation/authority.md`: rewrite guidance from
  `start_task(tool)` to `assign_task(worker, objective)`.
- `loop.py`: **no change.**

### Phase 5 — Persistence
- `TaskStore`: add `worker` column (which employee); reuse `create / set_status / get /
  list_recent / recover_stuck`. Additive migration only.
- `recover()`: leftover `queued`/`running` → `unknown` (unchanged honesty rule).
- `EmployeeMemory`: persists per-employee history/work summaries.

### Phase 6 — API-first surface (N10)
- REST: `GET /api/workers` (roster + status), `POST /api/workers/{name}/assign`,
  `GET /api/workers/{name}/activity`; keep `GET /api/tasks`.
- WS: broadcast `worker_*` / `task_*` (from Phase 3 publishing).
- All responses **additive** so `web/Console.tsx` keeps working until the UI step.

### Phase 7 — Tests / regression net (don't-break proof)
- New: `tests/unit/test_supervisor.py`, `test_employee_budgets.py`,
  `test_roster.py`; a fake employee for fast tests (replaces `InlineExecutor` role).
- Assert: `assign_task` returns an id; `task_status` shape stable; budget breach →
  honest status; `recover()` marks stuck `unknown`.
- Remove/replace tests for deleted classes.
- **Gate:** `python -m pytest tests/unit -q` + `ruff check app main.py tests` green,
  plus a live CLI smoke turn (`python main.py`, ask for background work).

### Phase 8 — Record D28 (permission philosophy)
- Only after Phase 7 passes. Document N5, the immutable hard-block list, and the
  audit/verify guardrails in `DECISIONS.md`.

### Phase 9 — UI (later, user-deferred)
- Roster + activity panel; consume Phase 6 endpoints/events.

---

## 5. The immutable hard-block list (N5, Phase 8)

A tiny, non-negotiable deny-set the `PermissionEngine` enforces **before** anything else,
which SAGE cannot override. Candidate classes (finalize in Phase 8):
- filesystem destruction outside the workspace sandbox (`rm -rf /`, `~`, disk devices);
- writes escaping the sandbox root;
- piping secrets/credentials to the network (exfiltration);
- privilege escalation (`sudo`, setuid, raw device writes).
Everything **else** → SAGE judges; engine only backstops. All decisions logged.

---

## 6. Safety limits (N8)

Enforced by the Supervisor around every employee assignment:
- `max_iterations` (loop turns),
- `max_tokens` (total across the assignment),
- `max_wall_seconds`,
- **kill switch** (cancel the asyncio task; mark `unknown` if mid-action).
On breach: `failed`, or `unknown` when the outcome is truly indeterminate.

---

## 7. Files: create / change / delete

**Create**
- `app/employees/__init__.py`
- `app/employees/employee.py`
- `app/employees/supervisor.py`
- `app/employees/roster.py`
- `app/employees/memory.py`
- `app/employees/budgets.py`
- `app/employees/roles/alpha.md`
- `tests/unit/test_supervisor.py`, `test_employee_budgets.py`, `test_roster.py`

**Change**
- `app/application.py` (dispatcher → supervisor; tool wiring)
- `app/tools/builtin.py` (`start_task` → `assign_task`; tool filter flag)
- `app/persistence/store.py` (`TaskStore.worker`; additive migration)
- `app/conversation/tools.md`, `app/conversation/authority.md`
- `web/backend.py` (Phase 6 endpoints/events)
- `docs/HOW_IT_WORKS.md`, `docs/DECISIONS.md` (contract + D28)

**Delete**
- `app/execution/worker.py`
- `app/execution/executor.py`
- (likely) `app/execution/protocol.py` — reintroduce only if OS isolation returns
- tests for the deleted classes

---

## 8. Open decision points (RESOLVED)

1. **Execution model** → (a) in-process lazy async.
2. **`worker_eligible`** → rename to **`employee_eligible`**.
3. **Employee memory/workspace** → separate store **and** workspace per employee
   (`data/employees/<name>/`).
4. **Roster naming** → **Asta, Otto, Tony, Alpha, Charlie**; lowercase core ids
   (`employee-asta`), cased display names in UI/CLI.

**Build intent:** register all five employees, but wire only `asta` to a role card first;
the plumbing is roster-shaped from day one, adding the other four is data only.

---

## 9. Definition of done (this track)
- Old process system gone; no references remain (`grep` clean).
- One employee (`alpha`) does real background work via `assign_task`; `task_status`
  reports it; budgets enforced; `recover()` honest.
- Full unit suite + `ruff` green; live CLI smoke turn works.
- REST/WS surface exists and the existing UI still functions (additive).
- D28 recorded.
