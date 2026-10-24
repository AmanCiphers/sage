# AGENTS.md — Working Notes for Agents on This Repository

This file tells any agent (AI or human) working on **SAGE** the rules for
leaving the codebase and docs in a consistent state.

## Non-negotiable: keep the docs current

**Every change that alters behavior, architecture, tools, prompts, or the
data model MUST update the docs as part of the same task.** Do not finish a
task without documenting it. This is the single most important rule here.

Where to record what:

| Change | Doc |
|--------|-----|
| Any behavioral/architectural change (including small ones) | `docs/CHANGELOG.md` (Part A, newest at the top of the section) |
| A formal architectural decision with rationale | `docs/DECISIONS.md` (add a `D<n>` entry in the appropriate phase) |
| A design that is planned but NOT yet built | clearly mark it **Planned / Not built** in the relevant doc |
| How the running system works | `docs/HOW_IT_WORKS.md` |
| Full target architecture / long-term design | `docs/ArchitectureV2.md` |

Conventions for each entry:
- **Date** — when the change landed.
- **Area** — Web UI / Permissions / Employees / Tools / Agents / Config / etc.
- **Change** — what changed.
- **Files** — where (paths).
- **Why** — the reason (the important part; never skip it).

## Rule of thumb

- If your change is small but changes behavior → add a **CHANGELOG** entry.
- If your change is a real decision (a tradeoff, a design choice, a direction
  change) → also add a **DECISIONS** entry.
- If your change adds a *capability* that is intended but not fully wired →
  mark it clearly as **Planned / Not built** in the architecture doc so the
  reader knows it isn't live yet.
- Never let a doc claim something is built when it isn't (that caused real
  staleness — e.g. D14 said "no embeddings" while hybrid RAG is live).

## When you change code

1. Read the relevant docs first if you're touching an area with a doc.
2. Make the change.
3. **Update the docs in the same task** — do not defer it.
4. Run the checks (see below) before considering the task done.

## Verification commands

```bash
# Tests
python -m pytest tests/unit -q

# Lint
ruff check app main.py

# Web build (only if you touched web/)
cd web && npm run build
```

## Conventions

- Follow existing code style; do not add comments unless asked.
- Keep responses concise.
- Commit only when explicitly asked; include the doc changes in the commit.
- If the task involves a new tool, add it to `app/tools/` following the
  existing pattern (`ToolDefinition` + registration) and document it in
  CHANGELOG + tools.md.