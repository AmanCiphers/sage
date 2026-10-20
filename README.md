# SAGE — Strategic Advisory & Guidance Engine

A personal agent: an agentic loop with memory, tools, background workers,
approvals, and a scheduler — plus a Next.js control UI.

See `ArchitectureV2.md` for the design and `docs/DECISIONS.md` for the
running log of decisions (D1+). `docs/HOW_IT_WORKS.md` explains the
runtime. `docs/CHANGELOG.md` records recent architecture changes and the
proposed employee-worker direction.

## Requirements

- Python >= 3.11 (a `.venv` is expected at the repo root)
- Node.js + npm (for the web UI under `web/`)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd web && npm install && cd ..
```

Provider keys live in `.env` (see `.env.example`). The primary provider is
Nebius `deepseek-ai/DeepSeek-V4-Flash-0731`; NVIDIA `openai/gpt-oss-20b` is
the fallback.

## Run the CLI

```bash
python main.py
```

Useful slash commands: `/profile`, `/memory`, `/search`, `/help`.

## Run the Web UI

One command starts the FastAPI backend (port 8000) and the Next.js
frontend (port 3000):

```bash
./start-web.sh
```

Then open http://localhost:3000.

### Running the pieces separately

The backend must be launched with the repo root on `PYTHONPATH`, otherwise
`from app...` imports fail:

```bash
PYTHONPATH="$PWD" python -m uvicorn web.backend:app --host 0.0.0.0 --port 8000 --reload
```

```bash
cd web && npm run dev   # http://localhost:3000
```

Backend endpoints: `POST /api/chat`, `GET /api/tools`, `GET /api/events`,
`GET/POST/DELETE /api/scheduler/jobs`, `GET/POST /api/scheduler/rules`
(`/api/proactive/rules` alias), `GET/POST /api/approvals`, and the chat
WebSocket at `ws://localhost:8000/ws/sage`.

## Tests and lint

```bash
python -m pytest tests/unit -q
ruff check app main.py tests
```

## Project layout

- `app/` — agent runtime (conversation, context, inference, tools,
  execution, memory, events, scheduler, permissions)
- `web/` — Next.js UI and `web/backend.py` (FastAPI adapter over the same
  `SAGEApplication` the CLI uses)
- `config/` — model catalog and settings
- `data/` — `memory.md` and `sage.db` (SQLite)
- `docs/` — decisions and architecture notes
- `tests/` — unit and integration tests
