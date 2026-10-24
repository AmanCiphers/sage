# SAGE — Self-Organizing Agent Gateway Engine

A local-first multi-agent system with specialized AI employees, RAG-powered context, and dual backend support (OpenCode + Hermes).

## Quick Start

```bash
# CLI
python main.py

# Web UI (requires 2 terminals)
./start-web.sh
# Or manually:
# Terminal 1: PYTHONPATH=. python -m uvicorn web.backend:app --reload
# Terminal 2: cd web && npm run dev
```

## Features

- **5 Specialized Employees** — Auto-routed by task type:
  - **Charlie** (Coder) — Next.js, React, Python, OpenCode, builds
  - **Alpha** (Research) — Analysis, investigation, deep research
  - **Asta** (Survey/Org) — Organization, listing, cataloging
  - **Otto** (Writer) — Drafting, documentation, content creation
  - **Tony** (Verification) — Validation, checking, quality assurance

- **Dual Agent Backends**
  - **OpenCode** — Nebius DeepSeek-V4-Flash (chat) + NVIDIA embeddings
  - **Hermes** — Local/remote LLM backend (configured, ready to test)

- **RAG Pipeline** — FTS5 full-text search + vector embeddings (NVIDIA Nemotron-3-Embed-1B, 1024-dim) with hybrid scoring (40% FTS5 + 60% cosine)

- **Per-Employee Isolation** — Each employee has own SQLite DB (with FTS5 + vector tables), workspace, and budget controls

- **Auto-Init on Startup** — Employee databases, schemas, and `agent_sessions` table created automatically

## Architecture

```
SAGE/
├── app/                    # Core application
│   ├── agents/            # OpenCode & Hermes backends
│   ├── employees/         # Employee factory, supervisor, budgets
│   ├── context/           # RAG context builder
│   ├── embeddings/        # Embedding providers (NVIDIA)
│   ├── application.py     # App wiring + DB auto-init
│   └── config.py          # Configuration
├── web/                   # Next.js + FastAPI web UI
├── data/employees/        # Per-employee DBs & workspaces
├── main.py                # CLI entry point
└── start-web.sh           # Web UI launcher
```

## Commands

```bash
# Tests
python -m pytest tests/unit -q

# Lint
ruff check app

# Web build
cd web && npm run build

# Backfill embeddings
python scripts/backfill_embeddings.py
```

## Configuration

- **OpenCode config**: `~/.config/opencode/opencode.jsonc`
- **Environment**: `.env` (API keys for Nebius, NVIDIA)
- **Model**: `nebius/deepseek-ai/DeepSeek-V4-Flash-0731` (provider prefix required)

## Status

| Component | Status |
|-----------|--------|
| CLI | ✅ Working |
| Web UI (Next.js + FastAPI) | ✅ Working |
| Unit Tests (160) | ✅ Passing |
| Lint (ruff) | ✅ Clean |
| Web Build | ✅ Clean |
| Employee DBs | ✅ Auto-init |
| OpenCode Backend | ✅ Working |
| Hermes Backend | ✅ Configured |
| RAG (FTS5 + Embeddings) | ✅ Working |
| Auto-assign Employee | ✅ Working |

## Known Issues

- FTS5 syntax errors on backslash (non-fatal, logged as warning)
- Per-employee log files not yet implemented
- OpenCode session resume (`--continue`) untested
- Hermes backend untested in practice

## License

MIT