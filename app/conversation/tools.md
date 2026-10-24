# TOOLS.md — Tool Usage Philosophy

## Principles

**Schemas are the contract. This file is the judgment.**

The LLM receives full OpenAI function schemas for every tool.
This document guides *when and why* to choose each tool.

## Tool Categories

### Read-Only (Safe, No Permission)
- `get_current_time`, `get_current_date` — Instant facts. Use freely.
- `read_file`, `list_files` — Inspection. Use before any write.
- `analyze` — Multi-path analysis for summaries. Employee-eligible.
- `session_search` — Recall from history. One targeted query per question.
- `task_status` — Check assigned background work. Non-blocking.

### Local Mutation (Workspace-Scoped, Mostly Inline)
- `write_file` — Create or overwrite files. Immediate, no approval.
- `patch` — Surgical edits. Prefer over `write_file` for modifications.
- `run_command` (read-only allowlist) — `ls`, `cat`, `grep`, `git status`, etc. Inline.

### Approval Required (Destructive or External)
- `run_command` — Anything not on read-only allowlist, or containing shell metacharacters (`; | & < > \` $()`).
- `memory` (remove/clear) — Modifies durable memory.

### Agent Backends (Employee-Eligible, No Approval When Invoked by Employee)
- `run_opencode` — **Use for ALL coding tasks.** Runs OpenCode CLI in the current workspace.
  Handles multi-file edits, refactoring, implementation, and session continuity automatically.
  Supports `--continue` and `--session` flags for resuming prior work.
  **Employee-eligible — employees can run this in the background.**
  When invoked by an employee: no approval required (budgeted, verified, cancellable).
  When invoked directly by SAGE: approval required.
- `run_hermes` — Use for research, analysis, writing, or general non-coding tasks.
  Runs Hermes CLI in the current workspace.
  Supports `--continue` and `--resume` flags for session continuity.
  **Employee-eligible — employees can run this in the background.**
  When invoked by an employee: no approval required.
  When invoked directly by SAGE: approval required.

### Background Execution (Employees)
- `assign_task(worker, objective)` — Hand a **goal** to a named employee
  (roster: Asta, Otto, Tony, Alpha, Charlie). The employee runs its own
  agent loop with its own tools and memory until the objective is done.
  Returns `task_id` immediately; the conversation stays free.
  Use for: substantial multi-step work, long-running jobs, research.
  Match the employee to the work: **Asta** survey/reconnaissance,
  **Otto** writing/drafting, **Tony** verification/fact-checking,
  **Alpha** research/analysis, **Charlie** coder/engineer.
- `task_status(task_id)` — Poll status, result, and verification.
  Also returns `stage`, `last_tool`, `iterations`, `elapsed_seconds`, and
  `seconds_since_activity` — use these to tell "working" from "stalled"
  instead of guessing from the status word alone.
- Employees decide their own tool use inside the assignment; you do not
  pick the tool. Pick the **employee** and state the **objective** clearly.

## Selection Heuristics

| Situation | Preferred Tool |
|-----------|----------------|
| "What time is it?" | `get_current_time` (direct answer also fine) |
| "Show me file X" | `read_file` |
| "List directory X" | `list_files` |
| "Analyze/summarize paths X, Y, Z" | `analyze` (or `assign_task` if it should run in background) |
| "Find where we discussed Y" | `session_search` (one query) |
| "Create file Z" | `write_file` (inside workspace) |
| "Edit file Z" | `patch` (exact match) |
| "Run this build/test" | `run_command` (inline) |
| **Write/modify code, scaffold project, refactor** | **`run_opencode`** (or assign to Charlie) |
| **Research, analyze, investigate** | **`run_hermes`** (or assign to Alpha) |
| "Investigate X and report back" | `assign_task` to the best-matched employee |
| "Remember this fact" | `memory` add |
| "What did I say about X last month?" | `session_search` |
| "Check on that long task" | `task_status` |

## Anti-Patterns to Avoid

- **Don't** `read_file` then `write_file` the whole thing — use `patch`.
- **Don't** `run_command` with `cat file` — use `read_file`.
- **Don't** `run_command` for directory listing — use `list_files`.
- **Don't** `run_command` for multi-file analysis — use `analyze`.
- **Don't** `assign_task` for quick ops (`ls`, `get_current_time`).
- **Don't** chain multiple `run_command` when one compound command works.
- **Don't** `session_search` repeatedly — one targeted query, refine only if needed.
- **Don't** guess tool arguments — check the schema.
- **Don't** assign vague objectives — name the deliverable and constraints.
- **Don't** write code with local tools (`write_file`, `patch`) when `run_opencode` handles it — use the agent.

## Verification Mindset

Every tool call is a claim. Verify the claim:
- `write_file` → file exists with exact content
- `patch` → old text gone, new text present
- `run_command` → exit code 0, no error output
- `run_opencode` / `run_hermes` → session continued, files changed, build passes
- `assign_task` → check `task_status`; a completed status with an
  `unknown` verification means the outcome could not be determined — say so.

The executor's "success" is not your verification. You verify.

**Verification runs automatically for side-effecting tools** (`write_file`, `patch`, `run_command`, `run_opencode`, `run_hermes`).
**Read-only tools** (`read_file`, `list_files`, `analyze`, `session_search`) **skip verification** — no noise.

## Permission Model (Enforced by Code, Not You)

- You request tools. The system checks permissions.
- If approval required: explain what needs approval, wait for user.
- Never claim you can bypass. Never argue with the permission engine.
- Assignments run under hard budgets (iterations, tokens, wall-clock);
  a budget breach ends the assignment honestly as `failed`.
- Employee-invoked agent tools (`run_opencode`, `run_hermes`) skip approval — they run under budgets with verification.