"""Factory for creating agent backends."""

from __future__ import annotations

from app.agents.interface import AgentBackend, AgentTask
from app.config import settings

# Import backends lazily to avoid circular imports
_opencode_backend = None
_hermes_backend = None


def get_opencode_backend(workspace: str) -> AgentBackend:
    """Get or create OpenCode backend for workspace."""
    global _opencode_backend
    if _opencode_backend is None:
        from app.agents.opencode import OpenCodeBackend
        _opencode_backend = OpenCodeBackend(
            workspace=workspace,
            opencode_path=settings.opencode_path,
            default_model=settings.opencode_default_model,
        )
    return _opencode_backend


def get_hermes_backend(workspace: str) -> AgentBackend:
    """Get or create Hermes backend for workspace."""
    global _hermes_backend
    if _hermes_backend is None:
        from app.agents.hermes import HermesBackend
        _hermes_backend = HermesBackend(
            workspace=workspace,
            hermes_path=settings.hermes_path,
            default_model=settings.hermes_default_model,
        )
    return _hermes_backend


def create_backend(backend_type: str, workspace: str) -> AgentBackend:
    """Create an agent backend by type."""
    if backend_type == "opencode":
        return get_opencode_backend(workspace)
    elif backend_type == "hermes":
        return get_hermes_backend(workspace)
    raise ValueError(f"Unknown backend type: {backend_type}")


async def run_agent_task(
    backend_type: str,
    workspace: str,
    objective: str,
    context_files: list[str] | None = None,
    context_summary: str = "",
    model: str | None = None,
    session_id: str | None = None,
) -> AgentTask:
    """Helper to run a task on an agent backend."""
    backend = create_backend(backend_type, workspace)
    await backend.start()

    task = AgentTask(
        objective=objective,
        workspace=workspace,
        context_files=context_files or [],
        context_summary=context_summary,
        model=model,
        session_id=session_id,
    )

    return task
