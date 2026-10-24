"""Shell execution: run_command."""

import asyncio
import json
import time

from app.permissions.command_policy import is_read_only_command
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.schemas import ToolInput

MAX_OUTPUT_CHARS = 20_000
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300


class RunCommandInput(ToolInput):
    command: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


async def run_command(command: str, timeout_seconds: int = 30) -> str:
    """Run a shell command in the current workspace with a hard timeout."""

    from app.tools.workspace import current_workspace

    workspace = current_workspace()
    workspace.mkdir(parents=True, exist_ok=True)

    timeout_seconds = max(1, min(timeout_seconds, MAX_TIMEOUT_SECONDS))
    started = time.perf_counter()

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return json.dumps({"error": f"Cannot start command: {exc}"})

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return json.dumps(
            {
                "error": f"Command timed out after {timeout_seconds}s",
                "command": command,
                "timeout_seconds": timeout_seconds,
            }
        )

    duration_ms = (time.perf_counter() - started) * 1000

    stdout = stdout_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
    stderr = stderr_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

    return json.dumps(
        {
            "command": command,
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": round(duration_ms),
        }
    )


def _needs_approval(arguments: dict) -> bool:
    try:
        return not is_read_only_command(arguments["command"])
    except (KeyError, TypeError):
        return True


def register_shell_tools(registry) -> None:
    registry.register(
        ToolDefinition(
            name="run_command",
            description=(
                "Run a shell command in the SAGE workspace directory. "
                "Read-only commands (ls, cat, grep, git status, ...) run "
                "immediately; anything that can change state requires user "
                "approval. Results include exit_code, stdout, stderr. "
                "NOT FOR CODING TASKS — do not use for npm, git, build, or scaffolding. "
                "Use run_opencode for all coding-related commands."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=RunCommandInput,
            destructive=False,
            employee_eligible=True,
            requires_approval=_needs_approval,
            handler=run_command,
        )
    )
