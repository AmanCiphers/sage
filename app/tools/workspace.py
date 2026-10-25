"""Per-turn workspace resolution and authorization for file and shell tools.

Tools are shared by reference between SAGE and its employees, so the
workspace a tool operates in cannot be a per-instance attribute. Instead it
is a context variable: SAGE runs in the global workspace
(`settings.workspace_path`), and an employee sets the variable to its own
workspace for the duration of its turn (see `Employee.work`).

Because asyncio tasks copy the current context, setting the variable inside
a running assignment task affects only that employee's turns and never leaks
into SAGE's conversation.

Similarly, `current_employee` tracks which employee is currently executing
so that agent tools (run_opencode, run_hermes) can route to the correct
per-employee session database.

Workspace Authorization:
- User explicitly authorizes a workspace (directory) via `authorize_workspace()` tool
- SAGE can freely access files within the authorized workspace
- Access outside authorized workspace requires explicit user permission via `authorize_workspace()`
- The authorized workspace persists for the session until changed or cleared
"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

_workspace_override: ContextVar[Path | None] = ContextVar(
    "sage_workspace_override", default=None
)

# Authorized workspace: the directory user explicitly authorized SAGE to access
_authorized_workspace: ContextVar[Path | None] = ContextVar(
    "sage_authorized_workspace", default=None
)

_current_employee: ContextVar[str | None] = ContextVar(
    "sage_current_employee", default=None
)


def current_workspace() -> Path:
    """Resolve the workspace root for the current turn."""
    
    override = _workspace_override.get()
    if override is not None:
        return override.expanduser().resolve()

    from app.config import settings

    return settings.workspace_path.expanduser().resolve()


def authorized_workspace() -> Path | None:
    """Get the currently authorized workspace (directory user explicitly authorized)."""
    return _authorized_workspace.get()


def authorize_workspace(path: Path | str) -> Path:
    """Explicitly authorize a workspace (directory) for SAGE to access.
    
    Returns the authorized workspace path. This grants SAGE permission to
    access files within this directory and its subdirectories without
    requiring additional permission prompts.
    """
    path = Path(path).expanduser().resolve()
    _authorized_workspace.set(path)
    return path


def clear_authorized_workspace() -> None:
    """Clear the authorized workspace, requiring re-authorization for any access."""
    _authorized_workspace.set(None)


def is_path_authorized(path: Path) -> bool:
    """Check if a path is within the authorized workspace."""
    from app.config import settings
    
    # Allow disabling authorization checks (for testing)
    if settings.disable_workspace_auth:
        return True
    
    authorized = _authorized_workspace.get()
    if authorized is None:
        return False
    try:
        path.resolve().relative_to(authorized.resolve())
        return True
    except ValueError:
        return False


def current_workspace() -> Path:
    """Resolve the workspace root for the current turn."""
    
    override = _workspace_override.get()
    if override is not None:
        return override.expanduser().resolve()

    from app.config import settings

    return settings.workspace_path.expanduser().resolve()


def use_workspace(path: Path | str):
    """Set the workspace override; returns the token for resetting."""
    
    return _workspace_override.set(Path(path))


def current_employee_key() -> str | None:
    """Return the key of the currently executing employee (e.g., 'charlie')."""
    return _current_employee.get()


def use_employee(key: str):
    """Set the current employee key; returns the token for resetting."""
    return _current_employee.set(key)


def reset_workspace(token) -> None:
    _workspace_override.reset(token)


def reset_employee(token) -> None:
    _current_employee.reset(token)
