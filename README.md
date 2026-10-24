# SAGE — Self-Organizing Agent Gateway Engine

> **A local-first, always-on AI assistant that doesn't just chat — it works.**  
> SAGE is the **manager**; specialized AI employees (Charlie, Alpha, Asta, Otto, Tony) execute goals autonomously with full tool access, verification, and crash-proof persistence.

---

## Why SAGE Exists

| Current AI Assistants | SAGE |
|-----------------------|------|
| Chat-only, no persistent memory | **Forever conversation** — raw history never deleted, auto-compacted |
| Single-threaded, blocks on long tasks | **Background execution** — `assign_task` returns instantly, conversation stays free |
| Trust the model's output | **Verify, don't trust** — deterministic re-checks on every file edit, command, API call |
| No accountability | **Operation Manager** — every side effect tracked, crash-recoverable, auditable |
| One model, one context | **Multi-agent roster** — 5 specialized employees with own memory, tools, budgets |
| Cloud-dependent | **Local-first** — SQLite, your API keys, your machine, your data |

---

## Live Demo (30 seconds)

```bash
# Terminal 1: Start the brain
python main.py

# Terminal 2: Start the web UI (Next.js + FastAPI)
./start-web.sh
```

Then ask SAGE:
> *"Build a Next.js dashboard with Tailwind, TypeScript, and a real-time SSE chart. Charlie, take this."*

**Watch**: Charlie (coder employee) spins up OpenCode, writes files, runs `npm install`, `npm run build`, verifies the build passes — all while you keep chatting in the main window.

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER                                      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      SAGE CORE (Single Process)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │Conversation  │  │   Context    │  │     Execution          │ │
│  │   Engine     │◄─┤   Engine     │  │     Engine             │ │
│  │ (agentic     │  │ (FTS5 +      │  │  ┌──────────────────┐  │ │
│  │  loop,       │  │  embeddings) │  │  │  Direct Tools    │  │ │
│  │  streaming)  │  │              │  │  │  (read, search,  │  │ │
│  └──────┬───────┘  └──────┬───────┘  │  │   memory, time)  │  │ │
│         │                 │          │  └────────┬─────────┘  │ │
│         └────────┬────────┘          │           │            │ │
│                  ▼                   │           ▼            │ │
│         ┌─────────────────┐          │  ┌──────────────────┐  │ │
│         │  SUPERVISOR     │◄─────────┤  │  EMPLOYEES       │  │ │
│         │  (assign_task,  │          │  │  ┌────────────┐  │  │ │
│         │   task_status,  │          │  │  │  Charlie   │  │  │ │
│         │   recover)      │          │  │  │  (Coder)   │  │  │ │
│         └────────┬────────┘          │  │  │  run_opencode│  │  │ │
│                  │                   │  │  └────────────┘  │  │ │
│                  ▼                   │  │  ┌────────────┐  │  │ │
│         ┌─────────────────┐          │  │  │  Alpha     │  │  │ │
│         │  PERMISSIONS    │          │  │  │ (Research) │  │  │ │
│         │  (deterministic,│          │  │  │  run_hermes│  │  │ │
│         │   approval-bound)│         │  │  └────────────┘  │  │ │
│         └────────┬────────┘          │  │  ┌────────────┐  │  │ │
│                  │                   │  │  │  Asta      │  │  │ │
│                  ▼                   │  │  │ (Org)      │  │  │ │
│         ┌─────────────────┐          │  │  └────────────┘  │  │ │
│         │  OPERATIONS     │          │  │  ┌────────────┐  │  │ │
│         │  (side-effect   │          │  │  │  Otto      │  │  │ │
│         │   tracking,     │          │  │  │ (Writer)   │  │  │ │
│         │   crash recovery)          │  │  └────────────┘  │  │ │
│         └────────┬────────┘          │  │  ┌────────────┐  │  │ │
│                  │                   │  │  │  Tony      │  │  │ │
│                  ▼                   │  │  │(Verification)  │  │  │ │
│         ┌─────────────────┐          │  │  └────────────┘  │  │ │
│         │  VERIFICATION   │          │  └──────────────────┘  │ │
│         │  (re-check real │          │                          │ │
│         │   world state)  │          └──────────────────────────┘ │
│         └─────────────────┘                                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌───────────┐  ┌───────────┐  ┌───────────┐
        │  OpenCode │  │  Hermes   │  │  Shell/   │
        │  (coding) │  │ (research)│  │  File Ops │
        └───────────┘  └───────────┘  └───────────┘
```

---

## What's Working Today ✅

| Component | Status | Details |
|-----------|--------|---------|
| **CLI** | ✅ | `python main.py` — streaming, tool rounds, approvals, slash commands |
| **Web UI** | ✅ | Next.js 14 + FastAPI, WebSocket live events, approvals over WS |
| **Agentic Loop** | ✅ | Real tool chains, compaction at 50% context, iterative summarization |
| **RAG (Hybrid)** | ✅ | FTS5 + NVIDIA Nemotron-3-Embed (1024-dim), 40/60 merge, async indexing |
| **Employees (5)** | ✅ | Charlie, Alpha, Asta, Otto, Tony — each with own DB, workspace, memory |
| **Auto-assignment** | ✅ | Keyword routing → correct employee (Next.js → Charlie, research → Alpha) |
| **Agent Backends** | ✅ | `run_opencode` (Nebius DeepSeek-V4-Flash), `run_hermes` — session resume |
| **Verification** | ✅ | Deterministic re-checks: file content, patch diffs, exit codes |
| **Crash Recovery** | ✅ | Startup recovers `UNKNOWN` tasks, reconciles operations, resumes sessions |
| **Tests** | ✅ | 160 unit tests, scripted fake provider, live smoke test |
| **Build/Lint** | ✅ | `ruff check app` clean, `npm run build` clean |

---

## The Employee Roster

| Employee | Role | Specialty | Backend | Tools |
|----------|------|-----------|---------|-------|
| **Charlie** | Coder | Next.js, React, Python, OpenCode, builds, refactoring | OpenCode | `run_opencode`, `run_command`, `write_file`, `patch`, `read_file` |
| **Alpha** | Research | Deep analysis, investigation, competitive intel, synthesis | Hermes | `run_hermes`, `read_file`, `list_files`, `session_search` |
| **Asta** | Organization | Cataloging, listing, structuring, inventory, taxonomy | Local | `read_file`, `list_files`, `memory`, `session_search` |
| **Otto** | Writer | Documentation, drafting, content, technical writing | Hermes | `run_hermes`, `write_file`, `patch`, `memory` |
| **Tony** | Verification | Validation, testing, quality gates, compliance checks | Local | `run_command`, `read_file`, `run_hermes`, `session_search` |

**Each employee has:**
- Private SQLite DB (`data/employees/<key>/sage.db`) with FTS5 + embeddings
- Private workspace (`data/employees/<key>/workspace/`)
- Own conversation loop, filtered tool registry, role card, budgets
- Agent session persistence (resume OpenCode/Hermes across assignments)

---

## Technical Differentiators

### 1. Authority Model: SAGE Judges, Engine Backstops
```
USER → SAGE (decides) → Employee (executes) → Tool/Agent → Verification → SAGE (narrates)
```
- **No LLM approves its own actions** — deterministic `PermissionEngine` with immutable hard-block list
- **Approval = bound data** — exact tool + args + resource + expiry, not a boolean
- **Post-hoc verification** — executor's "success" claim is re-checked against reality

### 2. Forever Conversation with Smart Compaction
- Raw `messages` table is **append-only, permanent** — never rewritten
- Compaction = iterative summary merge (previous summary + new middle) via 1 LLM call
- Fallback: static summary from recent user messages if summarizer fails
- Anti-thrash: 2 ineffective compactions in a row → blocked until effective one

### 3. Hybrid RAG: Keywords + Vectors, Zero Vector DB
```
User Query
    │
    ├─► FTS5 (SQLite) ──────► 40% weight ──►
    │                                      │
    └─► NVIDIA Embedding (1024d) ──► 60% weight ──► Merge → Inject into System Prompt
         (async, non-blocking)                Local cosine similarity
```
- Embedding generation = fire-and-forget background task on every message append
- Only `embed_query()` blocks the turn (~80ms); LLM call is the real latency (~2s)
- 103 messages indexed and searchable today

### 4. Background Execution Without Blocking
```python
# SAGE's turn returns IMMEDIATELY
task_id = assign_task("charlie", "Build a Next.js dashboard with Tailwind")

# User keeps chatting; "How's Charlie doing?" → task_status reads durable state
# Employee runs as lazy asyncio task with hard budgets (iters/tokens/wall-clock)
# SAGE can cancel_task anytime — no hard timeout killing valid 30-min work
```

### 5. Crash-Proof by Design
| Failure Point | Recovery |
|---------------|----------|
| SAGE crashes mid-turn | User turn persisted before 1st LLM call |
| Tool executes, SAGE crashes | Tool call persisted before execution |
| Background task dies | Startup marks `queued`/`running` → `UNKNOWN` |
| Operation halfway done | Operation Manager: `EXECUTING` → `UNKNOWN` on restart, reconcile |
| Agent session lost | `agent_sessions` table resumes OpenCode/Hermes via `--continue` |

---

## Roadmap: From Hackathon to Platform

### Phase 1: Polish & Harden (Weeks 1-2)
- [ ] Per-employee log files (`data/employees/<key>/sage.log`)
- [ ] OpenCode session resume verification (`--continue` across restarts)
- [ ] Hermes end-to-end exercise (research task → Alpha)
- [ ] Employee activity panel in Web UI (roster + live status)

### Phase 2: Proactive Intelligence (Weeks 3-4)
- [ ] **Scheduler** — cron jobs, recurring Workers, event-triggered tasks (ArchitectureV2 §36)
- [ ] **Proactive routines** — "Check email at 7am, summarize important" (ArchitectureV2 §56)
- [ ] **Current State** derived view — active projects, workers, priorities (ArchitectureV2 §64)
- [ ] **Memory extraction pipeline** — background LLM extracts facts from conversation (deferred from D13)

### Phase 3: Platform Features (Month 2)
- [ ] **Browser isolation** — task-scoped contexts, no cookie leakage (ArchitectureV2 §20)
- [ ] **Voice interface** — Wake word → Whisper → SAGE → Chatterbox TTS (ArchitectureV2 §57-59)
- [ ] **Notifications** — Web, native, Telegram, WhatsApp (ArchitectureV2 §55)
- [ ] **Artifacts system** — large outputs as references, not DB bloat (ArchitectureV2 §51)

### Phase 4: Enterprise Ready (Month 3)
- [ ] **Multi-user / workspaces** — isolated SAGE instances per team
- [ ] **Policy engine** — org-level permission rules, audit trails
- [ ] **Plugin SDK** — custom tools, employees, agent backends
- [ ] **Managed hosting option** — SAGE Cloud for teams who don't self-host

---

## Quick Start

```bash
# Prerequisites
# - Python 3.12+
# - Node 20+
# - NEBIUS_API_KEY, NVIDIA_API_KEY in .env

# 1. Clone & install
git clone https://github.com/your-org/sage.git
cd sage
pip install -e .
cd web && npm install && cd ..

# 2. Configure
cp .env.example .env
# Add your NEBIUS_API_KEY and NVIDIA_API_KEY

# 3. Run CLI
python main.py

# 4. Run Web UI (2 terminals)
./start-web.sh
# Terminal 1: FastAPI on :8000
# Terminal 2: Next.js on :3000
```

### Environment Variables
```bash
# Required
NEBIUS_API_KEY=sk-...          # DeepSeek-V4-Flash (chat)
NVIDIA_API_KEY=nvapi-...       # Nemotron-3-Embed (embeddings)

# Optional tuning
MAX_ITERATIONS=8               # SAGE's own loop cap
EMPLOYEE_MAX_ITERATIONS=0      # 0 = unlimited (default)
EMPLOYEE_MAX_WALL_SECONDS=0    # 0 = unlimited (default)
CONTEXT_WINDOW_TOKENS=32000
SAGE_WORKSPACE=data/workspace
```

---

## Project Structure

```
sage/
├── app/                          # Core application (modular monolith)
│   ├── agents/                   # External agent backends
│   │   ├── opencode.py           # OpenCode CLI wrapper
│   │   ├── hermes.py             # Hermes CLI wrapper
│   │   └── sessions.py           # Agent session persistence
│   ├── conversation/             # Agentic loop + system prompt
│   ├── context/                  # RAG: builder, compressor, tokens
│   ├── employees/                # Employee system (NEW)
│   │   ├── supervisor.py         # Frozen contract: assign/get_status/recover
│   │   ├── employee.py           # Per-employee agent loop
│   │   ├── roster.py             # Fixed 5-employee registry
│   │   ├── factory.py            # Per-employee DB, memory, workspace
│   │   └── budgets.py            # Hard limits + kill switch
│   ├── embeddings/               # NVIDIA provider + local cosine store
│   ├── execution/                # Verification, models
│   ├── inference/                # Provider router, fallback, streaming
│   ├── memory/                   # Bounded long-term memory (memory.md)
│   ├── operations/               # Side-effect tracking, crash recovery
│   ├── permissions/              # Deterministic engine + approvals
│   ├── persistence/              # SQLite schema, stores, FTS5
│   ├── scheduler/                # Jobs, proactive routines (planned)
│   └── tools/                    # Registry + 20 builtin tools
├── web/                          # Next.js 14 + FastAPI
│   ├── backend.py                # Thin adapter over SAGEApplication
│   └── src/                      # React frontend
├── data/                         # Runtime data (gitignored)
│   ├── sage.db                   # Main conversation DB
│   └── employees/                # Per-employee DBs + workspaces
├── main.py                       # CLI entry
├── start-web.sh                  # Web UI launcher
└── tests/unit/                   # 160 tests, scripted fake provider
```

---

## Investor Highlights

| Metric | Value |
|--------|-------|
| **Lines of code** | ~15,000 (Python + TypeScript) |
| **Test coverage** | 160 unit tests, deterministic, fast |
| **External deps** | 2 API providers (Nebius, NVIDIA free tiers) |
| **Infrastructure** | Zero — runs on laptop, SQLite only |
| **Time to "hello world"** | <30 seconds |
| **Architecture** | Modular monolith — extractable to services later |
| **IP** | Permission model, verification, employee architecture, hybrid RAG |

### Why Now?
- **Local-first AI** is the next wave (privacy, latency, cost)
- **Agent orchestration** is unsolved — everyone builds chat, few build *managers*
- **Developer tools** have highest willingness-to-pay (Charlie = coding employee)
- **Extensible platform** — employees = plugins, agent backends = integrations

---

## License

MIT — build on it, commercialize it, extend it.

---

## Contributing

We're in **build mode**. Issues and PRs welcome for:
- Employee role cards (markdown, easy to tune)
- Tool additions (file a PR with tests)
- RAG tuning (weights, thresholds, models)
- Web UI polish (Tailwind, shadcn/ui)

See `docs/DECISIONS.md` for architecture rationale, `docs/HOW_IT_WORKS.md` for runtime behavior, `docs/ArchitectureV2.md` for full spec.

---

**SAGE is the assistant that shows up, stays on, and ships code.**  
*Built by developers who wanted an AI that actually works — not just talks.*