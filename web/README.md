# SAGE Web UI

Frontend (Next.js 15) + Backend (FastAPI) for the SAGE Web Interface.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐
│  Next.js (3000) │────▶│  FastAPI (8000)  │
│   Frontend      │◀───│  Backend         │
│  (React/Tailwind)   │  SAGEApplication  │
└─────────────────┘     └──────────────────┘
                              │
                        ┌─────┴─────┐
                        ▼           ▼
                   ┌─────────┐ ┌──────────┐
                   │  SQLite │ │  Event   │
                   │ (sage.db)│ │   Bus    │
                   └─────────┘ └──────────┘
```

The **FastAPI backend** runs the actual `SAGEApplication` (same composition root as CLI/TUI). The **Next.js frontend** consumes REST + SSE + WebSocket endpoints.

## Quick Start

### Prerequisites
- Python 3.11+ with dependencies installed (`pip install -e .` from repo root)
- Node.js 18+ with npm
- `.env` configured at repo root (see [root README](../README.md))

### Start Both Servers

**Terminal 1 — Backend (port 8000):**
```bash
cd /Users/aman/codes/the-SAGE-project/sage
python web/backend.py
```
Output: `Uvicorn running on http://0.0.0.0:8000`

**Terminal 2 — Frontend (port 3000):**
```bash
cd /Users/aman/codes/the-SAGE-project/sage/web
npm run dev
```
Output: `Local: http://localhost:3000`

Open **http://localhost:3000** — the UI connects to the backend automatically.

### Production Build
```bash
cd web && npm run build && npm start
```
The production build hardcodes `localhost:8000` for SSE. For deployment, set `NEXT_PUBLIC_SSE_BASE` env var.

## API Endpoints (Backend)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/info` | Model + runtime info |
| POST | `/api/chat` | Single-turn chat (non-streaming) |
| POST | `/api/chat/stream` | **Streaming chat (SSE)** — deltas, tool events, approvals |
| POST | `/api/approvals/{id}/approve` | Approve pending action |
| POST | `/api/approvals/{id}/deny` | Deny pending action |
| GET | `/api/tasks` | Recent background tasks |
| GET | `/api/workers` | Employee roster status |
| POST | `/api/workers/{name}/assign` | Assign objective to employee |
| GET | `/api/workers/{name}/activity` | Employee assignment history |
| GET | `/api/notices` | Employee completion notices (polling) |
| GET | `/api/scheduler/jobs` | Scheduled jobs |
| GET | `/api/scheduler/rules` | Proactive rules |
| WS | `/ws/sage` | WebSocket for system events (worker, scheduler, proactive) |

## Frontend Features

- **Console** — Main chat with streaming, markdown rendering, tool activity lines, thinking indicator
- **Worker Panel** (side) — Live employee status + REST-polled task list + activity feed from WebSocket
- **Approvals** — Modal dialog (TUI-style) for consent prompts
- **System Events** — Broadcast via WebSocket, routed to panels (not chat feed)

## Key Implementation Notes

- **SSE streaming** bypasses Next.js dev proxy (`SSE_BASE = "http://localhost:8000"`) — see `Console.tsx` (A1 in CHANGELOG)
- **LoopEvents** (`llm_start`, `tool_start`, `tool_end`, `compaction`, `approval`) travel over SSE; raw system events over WebSocket
- **CORS** allows `http://localhost:3000` only
- **Same SAGEApplication** as CLI/TUI — zero behavior drift (D27)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "SAGE not initialized" | Backend not started or crashed — check terminal 1 |
| SSE not streaming (buffered) | Ensure you're hitting `localhost:8000` directly, not via Next.js proxy |
| WebSocket connection failed | Backend must be running; check port 8000 |
| CORS error | Only `localhost:3000` allowed; add origin to `backend.py` if different |
| Module import error in backend | Fix `sys.path.insert(0, "...")` in `backend.py` to point to repo root |

## File Structure

```
web/
├── backend.py              # FastAPI server (runs SAGEApplication)
├── package.json            # Next.js deps + scripts
├── next.config.ts          # Next.js config
├── tsconfig.json           # TypeScript config
├── tailwind.config.ts      # Tailwind CSS config
├── src/
│   ├── app/
│   │   ├── page.tsx        # Main page (Console + panels)
│   │   ├── layout.tsx      # Root layout
│   │   └── globals.css     # Global styles
│   ├── components/
│   │   ├── Console.tsx     # Chat console (SSE + WS)
│   │   ├── Worker.tsx      # Worker panel
│   │   ├── EmployeePanel.tsx
│   │   ├── Panel.tsx       # Base panel component
│   │   └── LogView.tsx
│   └── lib/
│       ├── store.ts        # Client state (chat, workers, events)
│       └── format.ts       # Formatting helpers
└── public/                 # Static assets
```