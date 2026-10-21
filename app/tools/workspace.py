"""Per-turn workspace resolution for file and shell tools.

Tools are shared by reference between SAGE and its employees, so the
workspace a tool operates in cannot be a per-instance attribute. Instead it
is a context variable: SAGE runs in the global workspace
(`settings.workspace_path`), and an employee sets the variable to its own
workspace for the duration of its turn (see `Employee.work`).

Because asyncio tasks copy the current context, setting the variable inside
a running assignment task affects only that employee's turns and never leaks
into SAGE's conversation.
"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

_workspace_override: ContextVar[Path | None] = ContextVar(
    "sage_workspace_override", default=None
)


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


def reset_workspace(token) -> None:
    _workspace_override.reset(token)
