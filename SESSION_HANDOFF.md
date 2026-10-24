# SAGE Session Handoff — 2026-10-24

## Current State: All Systems Green ✅

**Last working session:** 2026-10-24 ~14:55 UTC  
**Branch/Commit:** Not tracked (local changes only)  
**Python:** 3.12 | **Node:** 20+ | **OS:** macOS

---

## What's Working (All Green)

| Component | Status |
|-----------|--------|
| CLI (`python main.py`) | ✅ Starts, chats, tools work |
| Web UI (`./start-web.sh`) | ✅ Next.js + FastAPI, SSE + WS |
| Tests (`pytest tests/unit`) | ✅ 160 pass |
| Lint (`ruff check app`) | ✅ Clean |
| Web build (`npm run build`) | ✅ Clean |
| Employee DBs | ✅ Auto-init at startup |
| Agent sessions | ✅ `agent_sessions` table exists |
| OpenCode backend | ✅ Works with Nebius DeepSeek-V4-Flash |
| Hermes backend | ✅ Configured |
| RAG (FTS5 + embeddings) | ✅ Auto-injects context |
| Auto-assign employee | ✅ Routes to correct employee |

---

## Key Fixes This Session

### 1. Employee Budget Bug (Fixed)
**File:** `app/employees/budgets.py:71`
```python
# Before: max_wall_seconds=0 → 0 second limit (broken)
# After: max_wall_seconds=0 → unlimited (correct)
if self.budget.max_wall_seconds and elapsed > self.budget.max_wall_seconds:
```

### 2. Employee Auto-Assignment (New Feature)
**File:** `app/employees/supervisor.py`
- Added `assign_auto(objective)` + `_select_best_worker(objective)`
- Routes based on keywords:
  - **Charlie** (coder): nextjs, react, python, opencode, build...
  - **Alpha** (research): research, analyze, investigate...
  - **Otto** (writer): write, draft, document...
  - **Tony** (verification): verify, check, validate...
  - **Asta** (org): organize, list, catalog...

### 3. OpenCode Config (Fixed)
**File:** `~/.config/opencode/opencode.jsonc` + `app/config.py` + `app/agents/opencode.py`
- Model: `nebius/deepseek-ai/DeepSeek-V4-Flash-0731` (provider prefix required)
- Providers: Nebius (chat) + NVIDIA (embeddings)

### 3. Employee DB Auto-Init
**File:** `app/application.py` → `_initialize_employee_databases()`
- Creates all 5 employee DBs with full schema at startup
- Fixes `agent_sessions` table missing errors

---

## Employee Roster

| Key | Name | Role | Tools |
|-----|------|------|-------|
| `charlie` | Charlie | **Coder** | opencode, hermes, files, shell |
| `alpha` | Alpha | Research | hermes, files |
| `asta` | Asta | Survey | files, list, analyze |
| `otto` | Otto | Writer | write, hermes |
| `tony` | Tony | Verification | files, hermes |

**Workspace:** `data/employees/<key>/workspace/`  
**DB:** `data/employees/<key>/sage.db` (each has own SQLite + FTS + embeddings)

---

## RAG Pipeline (Working)

**Flow:** User query → FTS5 search + embedding search → merge (40% FTS5 + 60% cosine) → inject into system prompt  
**Embeddings:** NVIDIA `nemotron-3-embed-1b` (1024 dim) via free tier  
**Backfill:** `scripts/backfill_embeddings.py` — 103 messages indexed

---

## Open Issues / Known Quirks

| Issue | Status | Notes |
|-------|--------|-------|
| FTS5 syntax errors on `\` | Known | Non-fatal, logged as warning |
| Employee logs | Missing | No per-employee log files yet |
| OpenCode session resume | Untested | `--continue` should work |
| Hermes backend | Untested | CLI config done, not exercised |
| `assign_task` auto-select | Added | Use `auto_select=true` or `worker="auto"` |

---

## Quick Commands

```bash
# Start CLI
python main.py

# Start Web UI (2 terminals)
./start-web.sh
# OR manually:
# Terminal 1: PYTHONPATH=. python -m uvicorn web.backend:app --reload
# Terminal 2: cd web && npm run dev

# Tests
python -m pytest tests/unit -q

# Lint
ruff check app

# Web build
cd web && npm run build

# Backfill embeddings
python scripts/backfill_embeddings.py
```

---

## Next Steps (When You Return)

1. **Test employee auto-assignment end-to-end** — assign a real Next.js task to Charlie via `assign_task auto_select=true`
2. **Test OpenCode session resume** — verify `--continue` works across SAGE restarts
3. **Exercise Hermes** — assign a research task to Alpha
3. **Per-employee logs** — add logging to `data/employees/<key>/sage.log`
4. **Employee session resume** — verify `agent_sessions` table works for OpenCode/Hermes resume

---

## File Pointers (Key Entry Points)

| Area | File |
|------|------|
| App entry | `main.py` |
| Web backend | `web/backend.py` |
| App wiring | `app/application.py` |
| Employee supervisor | `app/employees/supervisor.py` |
| Employee factory | `app/employees/factory.py` |
| Budgets | `app/employees/budgets.py` |
| Context/RAG | `app/context/builder.py` |
| Embeddings | `app/embeddings/` |
| OpenCode backend | `app/agents/opencode.py` |
| Config | `app/config.py` + `.env` |
| OpenCode config | `~/.config/opencode/opencode.jsonc` |

---

**Safe to change directory now.** All tests pass, builds clean, CLI starts.