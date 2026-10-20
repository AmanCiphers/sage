#!/bin/bash
# Start both the FastAPI backend and the Next.js frontend for the SAGE Web UI.
# Run from anywhere:  ./start-web.sh

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Resolve the repository root (directory containing this script).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo -e "${BLUE}Starting SAGE Web UI...${NC}"

# Activate the virtualenv so backend dependencies (fastapi/uvicorn) resolve.
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo -e "${YELLOW}Warning: .venv not found; using system Python${NC}"
fi

# Install frontend dependencies if needed.
if [ ! -d "web/node_modules" ]; then
    echo -e "${BLUE}Installing npm dependencies...${NC}"
    (cd web && npm install)
fi

# PYTHONPATH must include the repo root so `from app...` imports resolve
# when uvicorn loads web/backend.py.
export PYTHONPATH="$ROOT"

echo -e "${GREEN}Starting FastAPI backend on port 8000...${NC}"
python -m uvicorn web.backend:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Give the backend time to start before the frontend connects.
sleep 3

echo -e "${GREEN}Starting Next.js frontend on port 3000...${NC}"
(cd web && npm run dev) &
FRONTEND_PID=$!

cleanup() {
    echo -e "\n${YELLOW}Stopping SAGE Web UI...${NC}"
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

echo -e "${GREEN}Both services started!${NC}"
echo -e "${BLUE}Frontend:    http://localhost:3000${NC}"
echo -e "${BLUE}Backend API: http://localhost:8000${NC}"
echo -e "${BLUE}WebSocket:   ws://localhost:8000/ws/sage${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop both services${NC}"

wait "$BACKEND_PID" "$FRONTEND_PID"
