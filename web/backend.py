"""FastAPI backend for SAGE Web UI.

Reuses SAGEApplication as the single composition root (D27).
Exposes REST + SSE + WebSocket endpoints for the Next.js frontend.
"""

import asyncio
import contextlib
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Add parent directory to path for app imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application import SAGEApplication
from app.conversation.models import AssistantResponse, LoopEvent, UserMessage
from app.events.models import Event
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Global SAGE application instance
sage_app: SAGEApplication | None = None

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._system_event_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._broadcast_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("ws_connected count=%d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info("ws_disconnected count=%d", len(self.active_connections))

    async def broadcast(self, message: dict[str, Any]):
        """Broadcast a message to all connected clients."""
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def start_broadcast_loop(self):
        """Background task that forwards system Events to WebSocket clients."""
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def _broadcast_loop(self):
        while True:
            try:
                event = await self._system_event_queue.get()
                if isinstance(event, Event):
                    await self._forward_system_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("ws_broadcast_error", error=str(e))
                await asyncio.sleep(1)

    async def _forward_system_event(self, event: Event):
        """Forward a raw system event plus a clean human summary.

        Panels filter by ``source`` (worker/scheduler/proactive/task) and show
        the short ``summary`` — never the full payload. We also lift the
        employee identity (worker/name/task_id) and stage/tool to the top
        level so the UI can group activity per employee without parsing the
        whole payload.
        """
        p = event.payload or {}
        msg = {
            "type": "event",
            "source": event.source,
            "event_type": event.event_type,
            "summary": self._summarize(event),
            "worker": p.get("worker"),
            "name": p.get("name"),
            "task_id": p.get("task_id"),
            "stage": p.get("stage"),
            "tool": p.get("tool"),
            "data": event.to_dict(),
        }
        logger.debug(
            "ws_forward",
            event_type=event.event_type,
            worker=p.get("worker"),
            summary=msg["summary"],
        )
        await self.broadcast(msg)

    @staticmethod
    def _summarize(event: Event) -> str:
        """Build a short, human-readable one-liner for a system event."""
        p = event.payload or {}
        et = event.event_type

        if et == "task_created":
            obj = p.get("objective") or p.get("task_id") or "task"
            return f"task queued · {obj}"
        if et == "task_started":
            return f"task started · {p.get('task_id', '?')}"
        if et == "task_completed":
            return f"task completed · {p.get('task_id', '?')}"
        if et == "task_failed":
            return f"task failed · {p.get('task_id', '?')} · {p.get('error', '')}".rstrip(" ·")
        if et == "worker_started":
            return f"worker started · {p.get('worker_id', p.get('task_id', '?'))}"
        if et == "worker_completed":
            return f"worker finished · {p.get('worker_id', p.get('task_id', '?'))}"
        if et == "worker_failed":
            wid = p.get("worker_id", p.get("task_id", "?"))
            err = p.get("error", "")
            return f"worker failed · {wid} · {err}".rstrip(" ·")
        if et == "approval_requested":
            return f"approval requested · {p.get('tool_name', '')}".rstrip(" ·")
        if et == "approval_granted":
            return f"approval granted · {p.get('approval_id', '')}".rstrip(" ·")
        if et == "approval_denied":
            return f"approval denied · {p.get('approval_id', '')}".rstrip(" ·")
        if et in ("write_file", "file_created", "file_written"):
            path = p.get("path") or p.get("file") or "file"
            return f"{path} was written"
        if et == "file_changed":
            return f"{p.get('path', 'file')} changed"
        if et == "scheduled_job_triggered":
            return f"job fired · {p.get('job_name', p.get('job_id', '?'))}"
        if et == "scheduled_job_completed":
            return f"job completed · {p.get('job_id', '?')}"
        if et == "scheduled_job_failed":
            return f"job failed · {p.get('job_id', '?')}"
        if et.startswith("proactive"):
            return f"{et.replace('_', ' ')} · {p.get('name', p.get('rule_id', ''))}".rstrip(" ·")
        if et == "worker_log":
            return str(p.get("message", p.get("log", "")))[:160]
        if et == "employee_assigned":
            return f"assigned: {p.get('objective', '')}".rstrip(": ")
        if et == "employee_activity":
            stage = p.get("stage", "")
            tool = p.get("tool")
            if stage == "llm_start":
                return "thinking…"
            if stage == "tool_start" and tool:
                return f"using {tool}"
            if stage == "tool_end" and tool:
                return f"finished {tool}"
            return stage.replace("_", " ") or "working"
        if et == "employee_finished":
            return "finished the assigned work"
        return et.replace("_", " ")

    async def stop(self):
        if self._broadcast_task:
            self._broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._broadcast_task

    async def enqueue_system_event(self, event: Event):
        logger.debug("ws_enqueue", event_type=event.event_type, source=event.source)
        with contextlib.suppress(asyncio.QueueFull):
            self._system_event_queue.put_nowait(event)


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global sage_app
    logger.info("starting_sage_backend")
    sage_app = SAGEApplication()
    await sage_app.start()

    # Wire system events to WebSocket (LoopEvents go via SSE)
    sage_app.event_bus.subscribe_all(manager.enqueue_system_event)

    # Start WebSocket broadcast loop
    await manager.start_broadcast_loop()

    yield

    logger.info("stopping_sage_backend")
    await manager.stop()
    await sage_app.stop()


app = FastAPI(title="SAGE Backend", lifespan=lifespan)

# CORS for Next.js dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str


class ApprovalRequest(BaseModel):
    approved: bool


class AssignRequest(BaseModel):
    objective: str


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/info")
async def info():
    if not sage_app:
        return {
            "model": "unknown",
            "transport": "unknown",
            "runtime": "unknown",
            "store": "unknown",
            "endpoint": "unknown",
        }
    router = sage_app.conversation_engine.inference_manager.router
    primary = router.route(
        __import__("app.inference.models", fromlist=["ModelRequirements"]).ModelRequirements(
            tool_calling=True
        )
    )
    return {
        "model": primary.metadata.model_id,
        "transport": "fastapi + websocket",
        "runtime": "python 3.12",
        "store": "sqlite",
        "endpoint": "POST /api/chat/stream",
    }


@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not sage_app:
        return {"error": "SAGE not initialized"}
    response: AssistantResponse = await sage_app.conversation_engine.handle(
        UserMessage(content=request.message)
    )
    return {
        "content": response.content,
        "pending_approval": response.pending_approval,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "latency_ms": response.latency_ms,
        "model": response.model,
    }


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    if not sage_app:
        return StreamingResponse(
            iter(["data: {\"error\": \"SAGE not initialized\"}\n\n"]),
            media_type="text/event-stream",
        )

    # Queues for streaming
    delta_queue: asyncio.Queue[str | None] = asyncio.Queue()
    loop_event_queue: asyncio.Queue[dict | None] = asyncio.Queue()
    response_holder: dict[str, AssistantResponse] = {}

    async def on_delta(delta: str):
        await delta_queue.put(delta)

    async def on_loop_event(event: LoopEvent):
        """Convert LoopEvent to frontend SSE format."""
        if event.type == "llm_start":
            msg = {"stage": "thinking", "handler": "chat", "type": "stream"}
        elif event.type == "compaction":
            msg = {"notice": "compaction"}
        elif event.type == "tool_start":
            import json as json_lib

            args = json_lib.dumps(event.arguments) if event.arguments else ""
            msg = {"stage": "tool", "name": event.tool_name, "arguments": args}
        elif event.type == "tool_end":
            msg = {"tool": event.tool_name, "ok": event.success, "summary": event.error or ""}
        elif event.type == "approval":
            # Build a clear description with the actual command being run
            tool_name = event.tool_name
            resource = event.resource
            
            # Build a clear description with the actual command
            if tool_name == "run_command":
                cmd = event.resource or ""
                description = f"Run command: {cmd}"
                target = cmd[:200] + ("..." if len(cmd) > 200 else "")
            elif tool_name == "run_opencode":
                obj = ""
                try:
                    import json
                    args = json.loads(event.resource or "{}")
                    obj = args.get("objective", "")
                except:
                    pass
                description = f"OpenCode: {obj[:100]}{'...' if len(obj) > 100 else ''}"
                target = obj[:200] + ("..." if len(obj) > 200 else "")
            elif tool_name == "run_hermes":
                obj = ""
                try:
                    import json
                    args = json.loads(event.resource or "{}")
                    obj = args.get("objective", "")
                except:
                    pass
                description = f"Hermes: {obj[:100]}{'...' if len(obj) > 100 else ''}"
                target = obj[:200] + ("..." if len(obj) > 200 else "")
            elif tool_name == "run_opencode":
                obj = ""
                try:
                    import json
                    args = json.loads(event.resource or "{}")
                    obj = args.get("objective", "")
                except:
                    pass
                description = f"OpenCode: {obj[:100]}{'...' if len(obj) > 100 else ''}"
                target = obj[:200] + ("..." if len(obj) > 200 else "")
            elif tool_name == "write_file":
                path = event.resource or ""
                description = f"Write file: {path}"
                target = path
            elif tool_name == "patch":
                path = event.resource or ""
                description = f"Patch file: {path}"
                target = path
            elif tool_name == "write_file":
                path = event.resource or ""
                description = f"Write file: {path}"
                target = path
            elif tool_name == "patch":
                path = event.resource or ""
                description = f"Patch file: {path}"
                target = path
            else:
                description = f"Run {tool_name}"
                target = event.resource or ""
            
            msg = {
                "approval": {
                    "id": event.approval_id,
                    "description": description,
                    "target": target,
                    "worker_task_id": None,
                }
            }
        else:
            return
        await loop_event_queue.put(msg)

    async def run_turn():
        # Temporarily override on_event to capture LoopEvents
        original_on_event = sage_app.conversation_engine.on_event
        sage_app.conversation_engine.on_event = on_loop_event
        try:
            response = await sage_app.conversation_engine.handle_stream(
                UserMessage(content=request.message),
                on_delta=on_delta,
            )
            response_holder["response"] = response
        finally:
            sage_app.conversation_engine.on_event = original_on_event
            await delta_queue.put(None)
            await loop_event_queue.put(None)

    async def event_generator():
        encoder = json.JSONEncoder()
        task = asyncio.create_task(run_turn())

        delta_done = False
        loop_done = False

        while not (delta_done and loop_done):
            # Wait for next item from either queue
            delta_task = asyncio.create_task(delta_queue.get())
            loop_task = asyncio.create_task(loop_event_queue.get())

            done, pending = await asyncio.wait(
                [delta_task, loop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for completed in done:
                item = completed.result()
                if item is None:
                    # Mark the corresponding queue as done
                    if completed is delta_task:
                        delta_done = True
                    else:
                        loop_done = True
                elif isinstance(item, str):
                    yield f"data: {encoder.encode({'delta': item})}\n\n"
                elif isinstance(item, dict):
                    yield f"data: {encoder.encode(item)}\n\n"

            for p in pending:
                p.cancel()

        await task

        response = response_holder.get("response")
        if response:
            if response.pending_approval:
                pa = dict(response.pending_approval)
                pa.setdefault("id", pa.get("approval_id"))
                yield f"data: {encoder.encode({'approval': pa})}\n\n"
            yield f"data: {encoder.encode({'done': True})}\n\n"
        else:
            yield f"data: {encoder.encode({'error': 'No response generated'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/approvals/{approval_id}/approve")
async def approve_approval(approval_id: str):
    if not sage_app:
        return {"error": "SAGE not initialized", "status": "failed"}
    try:
        response: AssistantResponse = await sage_app.approve_pending(approval_id)
        return {"response": response.content, "status": "ok"}
    except Exception as e:
        logger.exception("approve_failed", approval_id=approval_id, error=e)
        return {"error": f"Approval failed: {e!s}", "status": "failed"}


@app.post("/api/approvals/{approval_id}/deny")
async def deny_approval(approval_id: str):
    if not sage_app:
        return {"error": "SAGE not initialized", "status": "failed"}
    try:
        response: AssistantResponse = await sage_app.deny_pending(approval_id)
        return {"response": response.content, "status": "ok"}
    except Exception as e:
        logger.exception("deny_failed", approval_id=approval_id, error=e)
        return {"error": f"Denial failed: {e!s}", "status": "failed"}


@app.get("/api/scheduler/rules")
async def list_scheduler_rules():
    if not sage_app or not sage_app.proactive:
        return {"rules": []}
    rules = await sage_app.proactive.list_rules()
    return {
        "rules": [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "description": r.description,
                "trigger": r.trigger_event_type,
                "action": r.action_event_type,
                "enabled": r.enabled,
                "cooldown": r.cooldown_seconds,
                "fire_count": r.fire_count,
            }
            for r in rules
        ]
    }


@app.get("/api/proactive/rules")
async def list_proactive_rules():
    return await list_scheduler_rules()


@app.get("/api/tasks")
async def list_tasks(limit: int = 50):
    if not sage_app:
        return {"tasks": []}
    try:
        store = sage_app.task_store
        tasks = await store.list_recent(limit=limit)
        return {"tasks": tasks}
    except Exception as e:
        logger.exception("list_tasks_failed", error=str(e))
        return {"tasks": []}


@app.get("/api/workers")
async def list_workers():
    """Roster snapshot: the five employees with standby/active status."""
    if not sage_app:
        return {"workers": []}
    return {"workers": sage_app.supervisor.roster_status()}


@app.post("/api/workers/{name}/assign")
async def assign_worker(name: str, request: AssignRequest):
    """Assign an objective to a named employee (API-first, N10)."""
    if not sage_app:
        return {"error": "SAGE not initialized", "status": "failed"}
    try:
        task_id = await sage_app.supervisor.assign(
            worker=name, objective=request.objective
        )
        return {"task_id": task_id, "worker": name, "status": "queued"}
    except KeyError as e:
        return {"error": str(e), "status": "failed"}
    except Exception as e:
        logger.exception("assign_failed", worker=name, error=str(e))
        return {"error": str(e), "status": "failed"}


@app.get("/api/workers/{name}/activity")
async def worker_activity(name: str, limit: int = 20):
    """Recent assignments for one employee."""
    if not sage_app:
        return {"activity": []}
    try:
        activity = await sage_app.supervisor.activity(name, limit=limit)
        return {"activity": activity}
    except KeyError as e:
        return {"error": str(e), "activity": []}


@app.get("/api/notices")
async def pending_notices():
    """Drain employee-completion notices for idle delivery.

    The front end polls this when SAGE is not mid-response, so a finished
    assignment is announced between turns without interrupting a reply.
    """
    if not sage_app:
        return {"notices": []}
    return {"notices": sage_app.completion_notifier.drain()}


@app.get("/api/scheduler/jobs")
async def list_scheduler_jobs():
    if not sage_app or not sage_app.scheduler:
        return {"jobs": []}
    jobs = await sage_app.scheduler.list_jobs()
    return {
        "jobs": [
            {
                "job_id": j.job_id,
                "name": j.name,
                "cron": j.cron_expression,
                "event_type": j.event_type,
                "enabled": j.enabled,
                "next_run": j.next_run_at,
                "run_count": j.run_count,
            }
            for j in jobs
        ]
    }


@app.websocket("/ws/sage")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "approve":
                    approval_id = msg.get("id")
                    if approval_id:
                        await sage_app.approve_pending(approval_id)
                elif msg.get("type") == "deny":
                    approval_id = msg.get("id")
                    if approval_id:
                        await sage_app.deny_pending(approval_id)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.exception("ws_error", error=str(e))
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
