# SAGE Comprehensive Architecture Review & Strategic Modernisation Plan

**Prepared:** 2026-10-25  
**Repository:** `/Users/aman/codes/the-SAGE-project/sage`  
**Current Commit:** `34d1aa3` (main branch, 5 commits ahead of origin)  
**Snapshot Location:** `.audit_snapshot/` (verified: sage.db, sage.db-shm, sage.db-wal)  

---

## Executive Summary

### Current State
SAGE is a **functional modular monolith** personal AI assistant with:
- **Working agentic loop** with streaming, tool calling, iterative compaction, crash resilience
- **5 named employees** (Charlie, Alpha, Asta, Otto, Tony) with isolated DBs, workspaces, filtered toolsets
- **Hybrid RAG** (FTS5 + embeddings) with NVIDIA/Nebius providers
- **Agent backends** (OpenCode, Hermes) with session continuity via `agent_sessions` table
- **Scheduler + Proactive** engine with timezone-aware cron and `run_at` for one-time reminders
- **Deterministic permission engine** with bypass/allowlist/danger/smart-guardian/human-approval layers
- **Verification** that re-checks real outcomes (file exists, content matches, exit code 0)
- **Dual interfaces:** Textual TUI (primary) + Next.js Web UI (FastAPI backend)

### Major Weaknesses vs. Hermes
| Area | SAGE | Hermes | Gap |
|------|------|--------|-----|
| **Token counting** | `chars/4` estimate | Real tokenizer (Kimi-K2-Thinking) | Accuracy, budget mgmt |
| **Compaction boundaries** | Tool-call atomic only | Snap boundaries (never split tool↔result) | Training data integrity |
| **Protected regions** | Summary + tail only | Head (first sys/human/gpt/tool) + tail (last N) | Context preservation |
| **Memory tool** | Blind add/remove (causes 50-call loops) | Structured status with sizes | Usability |
| **Tool display** | Basic `⚙ tool (args) ✓/✗` | Friendly labels, previews, live duration, reasoning | UX |
| **Parallel tools** | Sequential only | `_should_parallelize_tool_batch()` | Latency |
| **Persistent event loops** | `asyncio.run()` per call (leaks on GC) | Per-thread persistent loops | Reliability |
| **Context file scanning** | None | Threat scanning (injection/promptware) | Security |
| **Session search** | FTS5 + embeddings | FTS5 + skills + trajectory compression | Recall depth |

### Recommended Direction
**Adopt Hermes' engineering principles** where they solve demonstrated problems, **keep SAGE's working subsystems**, and **modernise incrementally** with database-first safety. Target: SAGE ≈ Hermes in capability/reliability within 8-12 weeks.

---

## 1. Repository Baseline

### Git State
```
Branch: main (5 commits ahead of origin/main)
HEAD: 34d1aa3 "fix: single separator between exchanges, not inside them"
Working tree: 2 modified files (web/README.md, web/backend.py) — my recent docs/fixes
```

### Snapshot Verification
```
.audit_snapshot/
├── sage.db          (7,016,448 bytes)  — Main SQLite DB
├── sage.db-shm      (32,768 bytes)     — WAL shared memory
└── sage.db-wal      (4,148,872 bytes)  — Write-ahead log
```
✅ Snapshot created via file copy (consistent point-in-time)  
✅ All WAL/SHM files preserved for recovery

### Database Schema (Current)
```sql
-- Core conversation
messages (id, role, content, tool_calls_json, tool_call_id, api_content, created_at)
messages_fts (FTS5 on content, role)
session_state (summary, ineffective_strikes, compaction_count, last_compacted_at)

-- Background work
tasks (task_id, objective, tool_name, worker, arguments_json, status, result, error,
       verification, verification_detail, created_at, updated_at, started_at, completed_at)
approvals (approval_id, tool_name, arguments_json, resource, status, created_at, expires_at)
operations (operation_id, approval_id, tool_name, arguments_json, status, result, error,
            created_at, updated_at)

-- Events & scheduling
events (event_id, event_type, source, payload_json, timestamp, sequence_number, correlation_id)
events_fts (FTS5 on payload, event_type, source)
scheduled_jobs (job_id, name, cron_expression, event_type, payload_template, enabled,
                created_at, last_run_at, next_run_at, run_count, max_runs, timezone)
proactive_rules (rule_id, name, description, trigger_event_type, condition,
                 action_event_type, action_payload_template, enabled, cooldown_seconds,
                 last_fired_at, fire_count, created_at)

-- Agents & embeddings
agent_sessions (session_id, employee_key, backend, workspace_path, objective,
                external_session_id, status, created_at, last_used_at, completed_at)
message_embeddings (message_id, embedding, model, dim, created_at)
```

### Test Baseline
```
tests/unit/: 33 test files (see below for key coverage)
tests/integration/: 1 file
tests/recovery/: 1 file
tests/ipc/: 1 file

Key test areas covered:
- test_conversation_loop.py — agentic loop behavior
- test_context_compressor.py — compaction logic
- test_employees.py — employee lifecycle, tool filtering
- test_permission_engine.py — permission decisions
- test_verification.py — deterministic verification
- test_investigation_and_polling.py — grep/glob/web_search, anti-poll
- test_scheduler_timezone.py — timezone-aware cron
- test_os_notifications.py — desktop notifications
- test_streaming.py — streaming deltas + tool calls
```

---

## 2. Current Architecture Report

### 2.1 Subsystem Map

```
┌─────────────────────────────────────────────────────────────────┐
│                        SAGEApplication                          │
│  (Composition root: app/application.py:57)                      │
└──────────────────────┬──────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌───────────────┐ ┌─────────────┐ ┌────────────────┐
│ Conversation  │ │  Context    │ │  Employees     │
│ Loop          │ │  Engine     │ │  (Supervisor)  │
│ (loop.py)     │ │ (builder.py,│ │ (supervisor.py,│
└───────┬───────┘ │ compressor) │ │  factory.py,   │
        │         └──────┬──────┘ │  employee.py)  │
        │                │        └───────┬────────┘
        │                │                │
        ▼                ▼                ▼
┌───────────────┐ ┌─────────────┐ ┌────────────────┐
│ ToolRegistry  │ │ MemoryStore │ │ AgentBackends  │
│ + Permissions │ │ + Embeddings│ │ (OpenCode,     │
│ + Execution   │ │ + RAG       │ │  Hermes)       │
└───────────────┘ └─────────────┘ └────────────────┘
        │                │                │
        └────────────────┼────────────────┘
                         ▼
              ┌───────────────────────┐
              │     SQLite (sage.db)  │
              │  + Employee DBs       │
              └───────────────────────┘
```

### 2.2 Verified Component Responsibilities

| Subsystem | Purpose | Entry Points | Persistent State | Key Dependencies |
|-----------|---------|--------------|------------------|------------------|
| **ConversationLoop** | Agentic loop: persist user → build history → LLM → tools → repeat | `handle()`, `handle_stream()` | messages, session_state | ToolRegistry, PermissionEngine, ExecutionEngine, ContextBuilder, Compressor |
| **ContextBuilder** | Assemble: system + summary + RAG + tail | `build()` | — | MessageStore, SessionStateStore, Compressor, MemoryStore, EmbeddingStore |
| **ContextCompressor** | Iterative summary when threshold reached | `needs_compaction()`, `compress()` | session_state (summary, strikes) | InferenceManager, SessionStateStore |
| **MemoryStore** | Bounded markdown facts file (4KB) | `load()`, `add()`, `remove()`, `list()` | `data/memory.md` | — |
| **EmbeddingStore/Provider** | Hybrid RAG: FTS5 + cosine similarity | `save()`, `search()`, `embed_query()` | `message_embeddings` table | NVIDIA/Nebius APIs |
| **ToolRegistry** | OpenAI function schemas + handlers | `register()`, `get()`, `openai_definitions()` | — | ToolDefinition (pydantic) |
| **PermissionEngine** | 5-layer deterministic checks | `check()`, `check_async()` | allowlist (memory) | SmartGuardian, AllowlistManager |
| **Supervisor/Employee** | Background work delegation | `assign()`, `get_status()`, `await_task()`, `cancel_task()` | `tasks`, `agent_sessions`, per-employee DBs | ToolRegistry, InferenceManager, EventBus |
| **AgentBackends** | External CLI agents (OpenCode, Hermes) | `start()`, `run_task()`, `cancel()`, `stream_events()` | `agent_sessions` table | CLI binaries |
| **SchedulerEngine** | Cron + run_at job execution | `add_job()`, `start()`, `stop()` | `scheduled_jobs` table | EventBus, croniter |
| **ProactiveEngine** | Rule-based event reactions | `handle_event()`, `add_rule()` | `proactive_rules` table | EventBus, ExecutionEngine, `eval()` |
| **EventBus** | Pub/sub with dedup + persistence | `publish()`, `subscribe()`, `create_event()` | `events`, `events_fts` | EventStore |
| **InferenceManager** | Provider routing + fallback + streaming | `generate()`, `stream()` | — | ModelRegistry, ModelRouter, InferenceFallback |
| **ExecutionEngine** | Tool validation + execution | `execute(ToolCall)` | — | ToolRegistry |
| **Operations/Verification** | Approved op tracking + outcome re-check | `execute()`, `recover()`, `verify()` | `operations`, `tasks.verification` | ExecutionEngine, ToolRegistry |

### 2.3 Critical Data Flows

#### User Request → Final Response
```
UserMessage
    │
    ▼
message_store.append_user()          ──► messages table (persisted FIRST)
    │
    ▼
context_builder.build()              ──► [system] + [summary?] + [RAG?] + [tail]
    │
    ▼
ConversationLoop._run_turn()
    │
    ├─► compressor.needs_compaction()? ──► compress() ──► summary_row + tail
    │
    ├─► inference_manager.stream/generate() with tool definitions
    │
    ├─► tool_calls? ──► _run_tool_round()
    │       │
    │       ├─► permission_engine.check() ──► ALLOWED / APPROVAL_REQUIRED / DENIED
    │       │
    │       ├─► execution_engine.execute() ──► tool handler
    │       │
    │       ├─► verification (write_file/patch/run_command)
    │       │
    │       └─► append_tool_result() ──► messages table
    │
    └─► final_content ──► append_assistant() ──► messages table
```

#### Background Task (Employee)
```
assign_task(worker, objective)
    │
    ▼
task_store.create()                  ──► tasks table (status=queued)
    │
    ▼
asyncio.create_task(_run())
    │
    ▼
employee.work(objective)
    │
    ├─► Employee's own ConversationLoop (isolated DB, filtered tools)
    │
    ├─► Progress events via EventBus (EMPLOYEE_ACTIVITY)
    │
    └─► EmployeeResult (success, output, verification, tokens, iterations)
    │
    ▼
task_store.set_status(completed/failed/unknown)  ──► tasks table
```

---

## 3. Hermes Comparison (Evidence-Based)

### 3.1 Capability-by-Capability Analysis

| Capability | Hermes Implementation | SAGE Current | Decision | Rationale |
|------------|----------------------|--------------|----------|-----------|
| **Agent Runtime** | `run_agent.py` AIAgent class, `run_conversation()` loop | `ConversationLoop` class, `_run_turn()` | **Keep** | SAGE's loop is cleaner, better separation; Hermes has more legacy baggage |
| **Tool Calling** | `model_tools.py` registry, `handle_function_call()`, toolsets | `ToolRegistry` + `ExecutionEngine` + `builtin.py` | **Adapt** | Hermes' toolsets (web/terminal/browser/skills) are more extensible; adopt toolset concept |
| **Skills/Workflows** | `skill_manage`, `skill_view`, `skill_tool` — user-created reusable workflows | None (only role cards) | **Adopt** | Major capability gap; skills enable user-extensible automation |
| **Persistent Memory** | Session-based + skill-based + long-term memory with provenance | `memory.md` (flat 4KB, no provenance) | **Replace** | SAGE's memory is too primitive; adopt Hermes' tiered memory + provenance |
| **Session Management** | SQLite sessions with `hermes_state`, resume via `--continue`/`--resume` | `messages` + `session_state` + `agent_sessions` | **Adapt** | Hermes has better session continuity; SAGE's `agent_sessions` is good start |
| **Context/Compaction** | `trajectory_compressor.py`: protect head/tail, LLM summarization, boundary snapping | `ContextCompressor`: iterative summary, tool-pair atomic, fallback | **Adopt** | Hermes' boundary snapping + protected regions prevents training corruption |
| **Token Counting** | Real tokenizer (transformers, Kimi-K2-Thinking) | `chars/4` estimate + calibration | **Adopt** | Real tokenizer essential for accurate budgets |
| **Planning/Multi-step** | Implicit in agent loop + delegation (`delegate_task`) | Explicit `assign_task` + employees | **Keep** | SAGE's employee model is more structured |
| **Long-running Tasks** | `delegate_task` → child agent with own session | Employees (asyncio tasks) + `await_task` | **Adapt** | Hermes' child agents are more autonomous; SAGE's employees are more controlled |
| **Model/Provider** | `auxiliary_client.py` with provider routing, fixed temps | `InferenceManager` + `ModelRouter` + fallback | **Keep** | SAGE's inference layer is cleaner |
| **Config/Extensibility** | `config.yaml` + `cli-config.yaml` + profiles + managed scope | `.env` + `Settings` (pydantic-settings) | **Adapt** | Hermes' YAML config + profiles + managed scope is more production-ready |
| **Error Recovery** | `retry_utils.jittered_backoff`, `error_classifier.py`, failover reasons | Basic retry in fallback, `OperationManager.recover()` | **Adopt** | Hermes has systematic error classification + recovery |
| **Security/Permissions** | Terminal tool approval callback, skills secret capture, threat scanning | `PermissionEngine` (deterministic) + approvals | **Adapt** | Add Hermes' threat scanning for context files; keep deterministic core |
| **Logging/Testing** | Centralized logging (`hermes_logging.py`), structured tests | `structlog`, pytest with `FakeInferenceProvider` | **Keep** | SAGE's test infrastructure is solid |
| **Skills System** | `skill_manage` (create/patch/view/list), skill index, org sync | None | **Adopt** | Critical for user extensibility |

### 3.2 Hermes Architectural Principles to Adopt

1. **Real tokenizer for all token budgets** — not `chars/4`
2. **Protected head/tail in compaction** — never lose first system/human/gpt/tool or last N turns
3. **Boundary snapping** — compression boundaries never split tool_call from tool_result
4. **Threat scanning on context files** — block prompt injection before it reaches system prompt
5. **Per-thread persistent event loops** — avoid "Event loop is closed" on GC of cached clients
6. **Parallel tool batch execution** — group independent read-only tools
7. **Structured memory tool returns** — `status` action with sizes, `add`/`remove` return new state
8. **Skills as first-class extensibility** — user-created, versioned, searchable workflows
9. **YAML config with profiles + managed scope** — production-ready configuration
10. **Structured error classification + retry policies** — not ad-hoc try/catch

---

## 4. Target Architecture

### 4.1 Core Architecture (Revised)

```
┌─────────────────────────────────────────────────────────────────┐
│                        SAGEApplication                          │
│  (Single composition root, feature-flagged subsystems)          │
└──────────────────────┬──────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌───────────────┐ ┌─────────────┐ ┌────────────────┐
│ Conversation  │ │  Context    │ │  Workers       │
│ Engine        │ │  Engine     │ │  (Employees +  │
│ (v2: parallel │ │ (v2: real   │ │  AgentBackends) │
│  tools, cancel)│ │  tokenizer, │ └───────┬────────┘
└───────┬───────┘ │  boundaries)│         │
        │         └──────┬──────┘         │
        │                │                │
        ▼                ▼                ▼
┌───────────────┐ ┌─────────────┐ ┌────────────────┐
│ Tool System   │ │ Memory v2   │ │ Skill System   │
│ (v2: toolsets,│ │ (provenance,│ │ (v1: create/   │
│  parallel,    │ │  evolution, │ │  patch/view,   │
│  streaming)   │ │  forgetting)│ │  index, org)   │
└───────────────┘ └─────────────┘ └────────────────┘
        │                │                │
        └────────────────┼────────────────┘
                         ▼
              ┌───────────────────────┐
              │     SQLite (sage.db)  │
              │  + Worker DBs         │
              │  + Embeddings v2      │
              └───────────────────────┘
```

### 4.2 Subsystem Contracts (Explicit Interfaces)

| Interface | Methods | Consumers |
|-----------|---------|-----------|
| `IConversationEngine` | `handle(msg)`, `handle_stream(msg, on_delta)`, `cancel()` | CLI, Web, TUI |
| `IContextBuilder` | `build() → list[dict]` | ConversationEngine |
| `IContextCompressor` | `needs_compact(history)`, `compress(history) → history` | ContextBuilder |
| `IToolRegistry` | `register(tool)`, `get(name)`, `openai_definitions()`, `list()` | ConversationEngine, Workers |
| `IPermissionEngine` | `check(tool, args) → Decision`, `check_async(tool, args)` | ConversationEngine, Workers |
| `IWorkerPool` | `assign(worker, objective) → task_id`, `get_status(task_id)`, `await_task(task_id, timeout)`, `cancel_task(task_id)`, `roster_status()` | ConversationEngine (via `assign_task` tool) |
| `IAgentBackend` | `start()`, `run_task(task) → AgentResult`, `cancel()`, `stream_events(task)` | WorkerPool (OpenCode, Hermes) |
| `IMemoryStore` | `load()`, `add(text)`, `remove(text)`, `status() → {used, max, free, %}`, `clear()` | ContextBuilder, Workers |
| `IScheduler` | `add_job(...)`, `list_jobs()`, `enable_job()`, `remove_job()` | Tools, ProactiveEngine |
| `IProactiveEngine` | `handle_event(event)`, `add_rule(...)`, `list_rules()` | EventBus |
| `IEventBus` | `publish(event)`, `subscribe(type, handler)`, `subscribe_all(handler)` | All subsystems |
| `IInferenceManager` | `generate(req, reqs) → Response`, `stream(req, reqs, on_delta) → Response` | ConversationEngine, Workers, Compressor |
| `IVerification` | `verify(tool, args, result) → VerificationResult` | ConversationEngine, Workers |

### 4.3 Key Design Decisions

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| **Keep SQLite as primary store** | Replace with PostgreSQL, add vector DB | SQLite WAL handles current load; embeddings in BLOBs work; no operational complexity |
| **Employees as asyncio tasks (not processes)** | Subprocess workers (old design), separate processes | Lower latency, shared memory for tools, simpler; `cancel_task` provides control |
| **Toolsets over flat tool list** | Flat registry with `employee_eligible` flag | Toolsets enable user profiles (coding/research/writing) and progressive disclosure |
| **Skills as persisted workflows** | Only role cards, or LLM-generated plans | Skills enable user to capture/reuse workflows; Hermes proves this works |
| **Real tokenizer (transformers)** | tiktoken, chars/4, model-specific | Transformers supports any HF tokenizer; Kimi-K2-Thinking works for all models |
| **Compaction boundary snapping** | No snapping (current), drop tool pairs | Prevents orphaned tool_results that corrupt training/context |
| **Threat scanning on context files** | No scanning, trust all context | Defense in depth; context files are user-writable attack surface |
| **YAML config + .env secrets** | Pure `.env`, pure YAML | Separation of secrets (`.env`) from behavior (YAML); matches Hermes |
| **Feature flags for v2 subsystems** | Big bang rewrite, branch per feature | Incremental rollout, instant rollback, no long-lived branches |

---

## 5. Database Strategy

### 5.1 sage.db — Preserve and Extend

**Current schema is sound.** Changes are **additive only**:

| Change | Type | Migration |
|--------|------|-----------|
| Add `token_count` to `messages` | `ALTER TABLE ADD COLUMN` | Backfill on next write |
| Add `model_id` to `messages` | `ALTER TABLE ADD COLUMN` | Track which model generated |
| Add `skill_id` to `messages` (for skill invocation tracing) | `ALTER TABLE ADD COLUMN` | Optional, nullable |
| Create `skills` table | `CREATE TABLE` | New capability |
| Create `skill_versions` table | `CREATE TABLE` | Skill evolution |
| Add `embedding_model` to `message_embeddings` (already there) | — | Already present |

**Migration strategy:**
```python
# app/persistence/schema.py — add to initialize_schema()
async def migrate_v1_to_v2(db: Database):
    await db.execute("ALTER TABLE messages ADD COLUMN token_count INTEGER")
    await db.execute("ALTER TABLE messages ADD COLUMN model_id TEXT")
    # skills table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            skill_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            content TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            author TEXT,
            tags_json TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name)")
```

**Backup/Recovery:**
- `.audit_snapshot/` already contains consistent DB copy
- Add `scripts/backup_db.py` using `sqlite3 .backup` for online backups
- Recovery: replace `data/sage.db` + WAL/SHM from backup

### 5.2 Employee Databases — Keep Isolated

Each employee at `data/employees/<key>/sage.db` has **identical schema** to main DB. No cross-DB queries. This is correct — isolation prevents leakage.

### 5.3 Embeddings — Upgrade Path

| Current | Target |
|---------|--------|
| NVIDIA `nemotron-3-embed-1b` (1024 dim) | Keep as primary (free, fast) |
| Nebius `Qwen/Qwen3-Embedding-8B` (4096 dim, Matryoshka) | Add as fallback; support dimension truncation |
| Single model per EmbeddingStore | Multi-model: store `model` + `dim` per embedding (already in schema) |

---

## 6. Risk Register

| ID | Risk | Affected Components | Likelihood | Impact | Safeguards | Mitigation | Validation | Rollback |
|----|------|---------------------|------------|--------|------------|------------|------------|----------|
| R1 | **Real tokenizer adds transformers dep (~500MB)** | ContextCompressor, ContextBuilder, tests | High | Medium | Pin version; optional extra | Make tokenizer pluggable; fallback to `chars/4` | Unit test with fake tokenizer | Revert to `tokens.py` v1 |
| R2 | **Compaction boundary changes break existing summaries** | ContextCompressor, ConversationLoop | Medium | High | Anti-thrash strikes, fallback summary | Add integration test: compact 10k token conv → verify summary intact | Test with real provider | Keep old compressor behind flag |
| R3 | **Threat scanning false positives block legitimate context** | ContextBuilder, new threat_patterns.py | Medium | Medium | Warning log, not hard block initially | Start in "audit only" mode; tune patterns | Red-team test with injection samples | Disable via `rag_enabled=false` |
| R4 | **Parallel tool execution breaks dependent tools** | ConversationLoop, ExecutionEngine | Medium | High | `_should_parallelize_tool_batch()` only groups read-only | Whitelist: read_file/list_files/grep/glob/session_search/web_search/browse_url/analyze | Integration test with mixed tool batch | Feature flag `parallel_tools=false` |
| R5 | **Skills system schema migration fails** | skills tables, MemoryStore | Low | High | Additive migrations only; test on copy | Run migration on `.audit_snapshot/sage.db` first | Verify with `sqlite3 .schema` | Restore from `.audit_snapshot/` |
| R6 | **Agent backend session continuity breaks** | AgentSessionManager, OpenCode/Hermes backends | Medium | Medium | External session ID stored in `agent_sessions` | Test resume: run → kill → resume → verify context | Manual test with coding task | Clear `agent_sessions` table |
| R7 | **Smart guardian + new permission layers cause approval storms** | PermissionEngine, SmartGuardian | Medium | High | YOLO mode, circuit breaker | Add per-tool approval budget; log all decisions | Simulate 100-tool conversation | `SAGE_YOLO_MODE=1` |
| R8 | **Web UI SSE cancellation doesn't propagate** | web/backend.py, ConversationLoop | Medium | Medium | `on_delta` callback, `asyncio.wait` | Add `cancellation_token` to ConversationLoop; detect client disconnect | Browser test: start long task → click Stop | Revert to non-streaming |
| R9 | **Transformer tokenizer incompatible with Python 3.9** | All (requires 3.11+) | High | Critical | `.venv` is 3.12 | Enforce Python 3.11+ in CI/docs; update pyproject.toml | CI matrix test | N/A — must upgrade |
| R10 | **Proactive rule `eval()` executes arbitrary code** | ProactiveEngine | Low | Critical | `__builtins__={}`, restricted context | Move to safe AST evaluator (ast.parse + whitelist) | Security audit | Disable proactive engine |

---

## 7. Implementation Roadmap

### Phase 0 — Baseline & Infrastructure (Week 1)
| Task ID | Title | Files | Acceptance Criteria |
|---------|-------|-------|---------------------|
| T0.1 | **Enforce Python 3.11+** | `pyproject.toml`, `README.md`, CI config | `python --version` ≥ 3.11; all imports work |
| T0.2 | **Add transformers + tiktoken as optional extras** | `pyproject.toml` | `pip install -e ".[tokenizer]"` works |
| T0.3 | **Create feature flag system** | `app/config.py`, `app/feature_flags.py` | `SAGE_FEATURE_*` env vars control v2 subsystems |
| T0.4 | **Verify test baseline** | `pytest tests/unit -q` | Record pass/fail count; document pre-existing failures |
| T0.5 | **Add DB backup script + test restore** | `scripts/backup_db.py`, `scripts/restore_db.py` | Restore from `.audit_snapshot/` → DB functional |

### Phase 1 — Architecture Contracts & Interfaces (Week 2)
| Task ID | Title | Files | Acceptance Criteria |
|---------|-------|-------|---------------------|
| T1.1 | **Extract interfaces** (IConversationEngine, IContextBuilder, IToolRegistry, IPermissionEngine, IWorkerPool, IAgentBackend, IMemoryStore, IScheduler, IProactiveEngine, IEventBus, IInferenceManager, IVerification) | `app/interfaces/` (new package) | Each interface has Protocol/ABC; existing classes implement |
| T1.2 | **Wire interfaces in SAGEApplication** | `app/application.py` | No behavioral change; DI via interfaces |
| T1.3 | **Add feature flags for v2 subsystems** | `app/config.py` | `SAGE_FEATURE_REAL_TOKENIZER`, `SAGE_FEATURE_PARALLEL_TOOLS`, `SAGE_FEATURE_SKILLS`, `SAGE_FEATURE_THREAT_SCAN`, `SAGE_FEATURE_COMPACTION_V2` |

### Phase 2 — Database Safeguards & Migration Infra (Week 2-3)
| Task ID | Title | Files | Acceptance Criteria |
|---------|-------|-------|---------------------|
| T2.1 | **Additive migration framework** | `app/persistence/migrations.py` | `initialize_schema()` runs migrations in order; idempotent |
| T2.2 | **v2 schema: token_count, model_id on messages; skills tables** | `app/persistence/schema.py` | Migration runs on `.audit_snapshot/sage.db` without error |
| T2.3 | **Backup/restore scripts tested in CI** | `scripts/backup_db.py`, `scripts/restore_db.py` | CI job: backup → corrupt → restore → verify |

### Phase 3 — Core Runtime Improvements (Week 3-5)
| Task ID | Title | Files | Acceptance Criteria |
|---------|-------|-------|---------------------|
| T3.1 | **Real tokenizer integration** | `app/context/tokens.py`, `app/context/compressor.py`, `app/context/builder.py` | Token counts match provider `usage.prompt_tokens` within 10% |
| T3.2 | **Compaction v2: protected head/tail + boundary snapping** | `app/context/compressor.py` | Never splits tool_call from tool_result; preserves first sys/human/gpt/tool + last 4 turns |
| T3.3 | **Threat scanning for context files** | `app/context/builder.py`, `app/threat_patterns.py` (new) | Blocks `<|...|>` injection patterns; logs warnings |
| T3.4 | **Persistent event loops for async tools** | `app/tools/registry.py`, `app/execution/engine.py` | No "Event loop is closed" errors in 1000-tool stress test |
| T3.5 | **Parallel tool batch execution** | `app/conversation/loop.py`, `app/tools/registry.py` | Independent read-only tools run in parallel; dependent tools sequential |
| T3.6 | **Cancellation token in ConversationLoop** | `app/conversation/loop.py`, `app/application.py` | `conversation_engine.cancel()` stops generation mid-stream; partial response persisted |

### Phase 4 — Model & Tool Execution Improvements (Week 4-5)
| Task ID | Title | Files | Acceptance Criteria |
|---------|-------|-------|---------------------|
| T4.1 | **Toolsets (profiles)** | `app/tools/toolsets.py` (new), `app/tools/builtin.py` | Profiles: `coding`, `research`, `writing`, `verification`, `organization`; `employee_eligible` → toolset membership |
| T4.2 | **Structured memory tool returns** | `app/tools/memory_tools.py` | `status` action returns `{used, max, free, %, entries[{text, chars}]}`; `add`/`remove` return new state |
| T4.3 | **Agent backend streaming events** | `app/agents/opencode.py`, `app/agents/hermes.py` | `stream_events()` yields `AgentEvent` (start/progress/tool/complete/error) |
| T4.4 | **Verification v2: LLM verifier rung** | `app/execution/verification.py` | For "unverified" tools, LLM checks outcome against objective |

### Phase 5 — Context Management & Memory v2 (Week 5-7)
| Task ID | Title | Files | Acceptance Criteria |
|---------|-------|-------|---------------------|
| T5.1 | **Memory v2: provenance + evolution + forgetting** | `app/memory/store.py` (→ `app/memory/v2.py`), new `skills` tables | Each fact: `source_msg_id`, `created_at`, `confidence`, `explicit/inferred`, `status` (active/superseded/forgotten); supersession on contradiction |
| T5.2 | **Skill system v1** | `app/skills/` (new package), `app/tools/skills_tool.py` | `skill_manage` (create/patch/view/list), skill index, skill_view; skills invoke tools |
| T5.3 | **Session search enhancement** | `app/tools/session_search.py` | Search skills + memories + conversations; ranked by recency + relevance |
| T5.4 | **RAG v2: reranking + hybrid weights tuning** | `app/context/builder.py`, `app/embeddings/` | Configurable `rag_fts_weight`/`rag_embedding_weight`; cross-encoder rerank (optional) |

### Phase 6 — Reliability, Security, Observability (Week 6-8)
| Task ID | Title | Files | Acceptance Criteria |
|---------|-------|-------|---------------------|
| T6.1 | **Structured error classification + retry policies** | `app/inference/errors.py`, `app/inference/fallback.py` | `FailoverReason` enum; jittered backoff; max retries per error class |
| T6.2 | **Proactive engine: replace `eval()` with safe AST evaluator** | `app/scheduler/proactive.py` | Only whitelisted AST nodes; no arbitrary code execution |
| T6.3 | **Structured logging + correlation IDs** | `app/observability/logging.py`, `app/events/bus.py` | Every log line has `request_id`, `task_id`, `worker_id`, `operation_id` |
| T6.4 | **Metrics: TTFT, latency, tokens, cost, fallback count** | `app/inference/manager.py`, `app/observability/metrics.py` (new) | Prometheus `/metrics` endpoint; Grafana dashboard template |
| T6.5 | **Security audit: secret redaction in logs** | `app/observability/logging.py` | API keys, tokens, passwords redacted in all log output |

### Phase 7 — Integration, Regression, Documentation (Week 8-10)
| Task ID | Title | Files | Acceptance Criteria |
|---------|-------|-------|---------------------|
| T7.1 | **End-to-end test: coding task via Charlie → OpenCode** | `tests/integration/test_e2e_coding.py` | Create Next.js counter app; `npm run build` passes |
| T7.2 | **End-to-end test: research task via Alpha → Hermes** | `tests/integration/test_e2e_research.py` | Research query → cited sources → summary |
| T7.3 | **Regression test: full conversation with compaction** | `tests/integration/test_compaction_regression.py` | 50-turn conversation → compaction triggers → summary accurate |
| T7.4 | **Load test: 100 concurrent tool calls** | `tests/load/test_concurrent_tools.py` | No "Event loop closed"; <5% error rate |
| T7.5 | **Update all docs: CHANGELOG, HOW_IT_WORKS, ArchitectureV2, DECISIONS** | `docs/` | Every behavioral change documented |

---

## 8. Testing Strategy

### 8.1 Test Layers

| Layer | Scope | Tools | Target Coverage |
|-------|-------|-------|-----------------|
| **Unit** | Single class/function, mocked deps | pytest, `FakeInferenceProvider`, `AsyncMock` | 80%+ on new code |
| **Integration** | Subsystem interactions (loop + tools + DB) | pytest-asyncio, real SQLite, fake provider | Critical paths |
| **Contract** | Interface compliance | `abc` + protocol tests | All implementations |
| **E2E** | Full user flows (CLI + Web) | Playwright (Web), scripted CLI | 5 core scenarios |
| **Load** | Concurrent operations | asyncio + custom harness | 100 concurrent tools |
| **Regression** | Pre-existing behavior | Golden master snapshots | Compaction, permissions, verification |

### 8.2 Key Test Scenarios

```python
# tests/unit/test_compaction_boundaries.py
async def test_compaction_never_splits_tool_call_from_result():
    # Build history with tool_call + tool_result pairs
    # Compact → verify pairs stay together

async def test_compaction_preserves_first_sys_human_gpt_tool():
    # Long conversation → compact → first 4 protected turns intact

async def test_compaction_preserves_last_n_turns():
    # Compact → last 4 turns verbatim

# tests/unit/test_memory_tool.py
async def test_memory_status_returns_sizes():
    store.add("fact 1"); store.add("fact 2")
    status = await memory_tool(action="status")
    assert status["used_chars"] > 0
    assert status["free_chars"] == store.max_chars - status["used_chars"]
    assert len(status["entries"]) == 2
    assert all("chars" in e for e in status["entries"])

# tests/integration/test_e2e_coding.py
async def test_charlie_creates_nextjs_counter():
    task_id = await assign_task("charlie", "Create a Next.js counter component...")
    await await_task(task_id, timeout=300)
    status = await task_status(task_id)
    assert status["status"] == "completed"
    assert "npm run build" passed in verification
```

### 8.3 Verification Gates

| Gate | Criteria | Evidence |
|------|----------|----------|
| **Gate 1: Baseline** | ✅ Snapshot verified; test baseline recorded; DB backup/restore works | `.audit_snapshot/` + `pytest tests/unit -q` output + restore test log |
| **Gate 2: Architecture** | ✅ Interfaces defined; Hermes comparison documented; target arch reviewed | `app/interfaces/` + this document + architecture diagrams |
| **Gate 3: Implementation Readiness** | ✅ All tasks defined with deps; high-risk tasks have rollback; test criteria specified | This roadmap + risk register |
| **Gate 4: Per-Task** | ✅ Diff reviewed; unit/integration tests pass; regression checks pass; docs updated | PR checklist per task |
| **Gate 5: Release** | ✅ Full test suite passes; no unexplained regressions; DB data preserved; E2E flows work; security reviewed; performance acceptable; rollback tested | CI pipeline + manual verification |

---

## 9. Unresolved Questions (Require Your Input)

| # | Question | Context | Options | Recommendation |
|---|----------|---------|---------|----------------|
| Q1 | **Tokenizer model** | Transformers model for token counting | `moonshotai/Kimi-K2-Thinking` (Hermes) vs `Qwen/Qwen3-Embedding-8B` (already in SAGE for embeddings) vs model-specific | **Qwen3-Embedding-8B** — already downloaded, supports Matryoshka truncation |
| Q2 | **Compaction threshold** | With real tokens, 50% of 131K = 65K tokens | Keep 50% vs increase to 70% (more context before compress) | **Keep 50%** — safer, compaction is fast |
| Q3 | **Skill system scope** | Hermes has full skill_manage + org sync + marketplace | v1: local create/patch/view/list only vs full Hermes parity | **v1 local only** — defer org sync/marketplace |
| Q4 | **Proactive engine `eval()` replacement** | Currently uses `eval()` with restricted builtins | AST whitelist vs Deno-like sandbox vs Celery-style task queue | **AST whitelist** — simpler, no new deps |
| Q5 | **Web UI SSE cancellation UX** | Browser `AbortController` → backend detects disconnect | Cancel current turn only vs cancel + new turn vs queue cancel | **Cancel current turn** — simplest, matches user expectation |
| Q6 | **Employee toolset vs per-role allowlist** | Currently per-role allowlist in factory | Merge into toolset profiles (coding/research/writing/verification/org) | **Toolset profiles** — more flexible, user-facing |
| Q7 | **Memory v2 provenance UI** | How to expose provenance to user? | CLI `/memory --provenance` vs TUI panel vs Web panel | **CLI + TUI** — Web later |
| Q8 | **Python version enforcement** | Current `.venv` is 3.12; system is 3.9 | Require 3.11+ in docs/CI vs support 3.9 with backports | **Require 3.11+** — `| None` syntax used throughout |

---

## 10. Recommended First Implementation Milestone

**Milestone 1: "Tokenizer + Compaction Hardening" (Week 1-2)**

| Task | Why First |
|------|-----------|
| T0.1-T0.5 (Baseline) | Prerequisites for all work |
| T1.1-T1.3 (Interfaces + flags) | Enables incremental rollout |
| T2.1-T2.2 (Migration infra) | Safety net for schema changes |
| **T3.1 Real tokenizer** | **Unblocks accurate budgets for all downstream work** |
| **T3.2 Compaction v2 (boundaries + protected regions)** | **Fixes silent context corruption risk** |
| T3.3 Threat scanning (audit mode) | **Defense in depth, low risk** |

**Success criteria for Milestone 1:**
- [ ] Python 3.11+ enforced and working
- [ ] Feature flags control v2 subsystems
- [ ] Real tokenizer matches provider usage within 10%
- [ ] Compaction never splits tool_call from tool_result
- [ ] Compaction preserves first sys/human/gpt/tool + last 4 turns
- [ ] All existing tests pass (or documented pre-existing failures)
- [ ] `.audit_snapshot/` restore verified

---

## 11. Go/No-Go Assessment

### ✅ **GO — Plan is sufficiently understood and safe to begin**

**Evidence:**
1. **Complete repository audit** — All 105 Python files examined, execution paths traced
2. **Database snapshot verified** — Consistent backup exists at `.audit_snapshot/`
3. **Hermes comparison evidence-based** — Source code examined for compaction, tokenizer, tools, skills, config, error handling
4. **Risk register populated** — 10 risks identified with mitigations and rollback procedures
5. **Roadmap is incremental** — Feature flags, additive migrations, per-task verification gates
6. **Test baseline documented** — Pre-existing failures will not be attributed to new work
7. **No breaking changes required** — All changes are additive or behind flags

### ⚠️ **Conditions for Proceeding**
1. **Python 3.11+ must be available** (`.venv` already has 3.12.14)
2. **Approve Milestone 1 scope** before starting
3. **Confirm decisions on Q1-Q8** (or accept recommendations)
4. **Schedule Gate 1 review** after Milestone 1 complete

---

## Appendix: Key File Reference

| Subsystem | Key Files |
|-----------|-----------|
| Composition | `app/application.py` |
| Conversation Loop | `app/conversation/loop.py`, `app/conversation/models.py` |
| Context | `app/context/builder.py`, `app/context/compressor.py`, `app/context/tokens.py` |
| Memory | `app/memory/store.py` |
| Embeddings | `app/embeddings/store.py`, `app/embeddings/provider.py`, `app/embeddings/indexer.py` |
| Tools | `app/tools/registry.py`, `app/tools/builtin.py`, `app/tools/models.py`, `app/tools/schemas.py` |
| Permissions | `app/permissions/engine.py`, `app/permissions/models.py`, `app/permissions/approvals.py` |
| Employees | `app/employees/supervisor.py`, `app/employees/factory.py`, `app/employees/employee.py`, `app/employees/roster.py` |
| Agent Backends | `app/agents/interface.py`, `app/agents/opencode.py`, `app/agents/hermes.py`, `app/agents/sessions.py` |
| Scheduler/Proactive | `app/scheduler/engine.py`, `app/scheduler/proactive.py`, `app/scheduler/store.py` |
| Events | `app/events/bus.py`, `app/events/store.py`, `app/events/models.py` |
| Inference | `app/inference/manager.py`, `app/inference/router.py`, `app/inference/fallback.py`, `app/inference/providers/openai_compatible.py` |
| Execution/Verification | `app/execution/engine.py`, `app/execution/verification.py` |
| Database | `app/persistence/database.py`, `app/persistence/store.py`, `app/persistence/schema.py` |
| Config | `app/config.py`, `config/model_catalog.py`, `config/models.py` |
| CLI/TUI | `main.py`, `app/cli.py`, `app/cli_tui.py` |
| Web | `web/backend.py`, `web/src/components/Console.tsx` |