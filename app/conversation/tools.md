# TOOLS.md — Tool Usage Philosophy

## Principles

**Schemas are the contract. This file is the judgment.**

The LLM receives full OpenAI function schemas for every tool.
This document guides *when and why* to choose each tool.

## Tool Categories

### Read-Only (Safe, No Permission)
- `get_current_time`, `get_current_date` — Instant facts. Use freely.
- `read_file`, `list_files` — Inspection. Use before any write.
- `analyze` — Multi-path analysis for summaries. Worker-eligible.
- `session_search` — Recall from history. One targeted query per question.
- `task_status` — Check background work. Non-blocking.

### Local Mutation (Workspace-Scoped, Mostly Inline)
- `write_file` — Create new files inside workspace. Immediate.
- `patch` — Surgical edits. Prefer over `write_file` for modifications.
- `run_command` (read-only allowlist) — `ls`, `cat`, `grep`, `git status`, etc. Inline.

### Approval Required (Destructive or External)
- `write_file` / `patch` — Existing file **outside** workspace.
- `run_command` — Anything not on read-only allowlist, or containing shell metacharacters (`; | & < > \` $()`).
- `memory` (remove/clear) — Modifies durable memory.

### Background Execution
- `start_task` — Dispatch any worker-eligible tool to a separate process.
  Returns `task_id` immediately. Conversation stays free.
  Use for: long commands, large file ops, anything that would block.
- Worker-eligible tools: `run_command`, `write_file`, `patch`, `analyze`.

## Selection Heuristics

| Situation | Preferred Tool |
|-----------|----------------|
| "What time is it?" | `get_current_time` (direct answer also fine) |
| "Show me file X" | `read_file` |
| "List directory X" | `list_files` |
| "Analyze/summarize paths X, Y, Z" | `analyze` (via `start_task` for background) |
| "Find where we discussed Y" | `session_search` (one query) |
| "Create file Z" | `write_file` (inside workspace) |
| "Edit file Z" | `patch` (exact match) |
| "Run this build/test" | `run_command` → if long, `start_task` |
| "Remember this fact" | `memory` add |
| "What did I say about X last month?" | `session_search` |
| "Check on that long task" | `task_status` |

## Anti-Patterns to Avoid

- **Don't** `read_file` then `write_file` the whole thing — use `patch`.
- **Don't** `run_command` with `cat file` — use `read_file`.
- **Don't** `run_command` for directory listing — use `list_files`.
- **Don't** `run_command` for multi-file analysis — use `analyze` (worker-eligible).
- **Don't** `start_task` for quick ops (`ls`, `get_current_time`).
- **Don't** chain multiple `run_command` when one compound command works.
- **Don't** `session_search` repeatedly — one targeted query, refine only if needed.
- **Don't** guess tool arguments — check the schema.

## Verification Mindset

Every tool call is a claim. Verify the claim:
- `write_file` → file exists with exact content
- `patch` → old text gone, new text present
- `run_command` → exit code 0, no error output
- `start_task` → check `task_status` + verification field

The executor's "success" is not your verification. You verify.

**Verification runs automatically for side-effecting tools** (`write_file`, `patch`, `run_command`).
**Read-only tools** (`read_file`, `list_files`, `analyze`, `session_search`) **skip verification** — no noise.

## Permission Model (Enforced by Code, Not You)

- You request tools. The system checks permissions.
- If approval required: explain what needs approval, wait for user.
- Never claim you can bypass. Never argue with the permission engine.
- Background tasks (`start_task`) re-check permissions at dispatch AND execution.