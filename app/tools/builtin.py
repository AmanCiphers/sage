import json
from datetime import datetime
from typing import Any

from pydantic import Field

from app.permissions.detection import is_read_only_command

from .models import ToolClassification, ToolDefinition
from .schemas import ToolInput


class CurrentTimeInput(ToolInput):
    pass


def get_current_time() -> str:
    return datetime.now().astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )


class CurrentDateInput(ToolInput):
    pass


def get_current_date() -> str:
    return datetime.now().astimezone().strftime(
        "%Y-%m-%d"
    )


class AssignTaskInput(ToolInput):
    worker: str
    objective: str
    auto_select: bool = False


class TaskStatusInput(ToolInput):
    task_id: str


class ScheduleJobInput(ToolInput):
    name: str
    cron_expression: str
    event_type: str
    payload_template: dict[str, Any] = Field(default_factory=dict)
    max_runs: int | None = None


class ListJobsInput(ToolInput):
    enabled_only: bool = False


class EnableJobInput(ToolInput):
    job_id: str
    enabled: bool = True


class RemoveJobInput(ToolInput):
    job_id: str


class EnableRuleInput(ToolInput):
    rule_id: str
    enabled: bool = True


class RemoveRuleInput(ToolInput):
    rule_id: str


class CancelTaskInput(ToolInput):
    task_id: str
    reason: str = "Cancelled by SAGE"


class RunOpenCodeInput(ToolInput):
    objective: str
    model: str | None = None
    session_id: str | None = None


class RunHermesInput(ToolInput):
    objective: str
    model: str | None = None
    session_id: str | None = None


class AddProactiveRuleInput(ToolInput):
    name: str
    description: str
    trigger_event_type: str
    condition: str
    action_event_type: str
    action_payload_template: dict[str, Any] = Field(default_factory=dict)
    cooldown_seconds: int = 300


class ListRulesInput(ToolInput):
    enabled_only: bool = True


class AnalyzeInput(ToolInput):
    paths: list[str]
    objective: str


async def analyze(paths: list[str], objective: str) -> str:
    """Analyze files and directories to produce a summary.
    
    Uses read_file and list_files internally. Employee-eligible for background execution.
    """  # noqa: W293
    from pathlib import Path

    from app.tools.file_tools import list_files, read_file
    from app.tools.workspace import current_workspace

    results = []
    workspace = current_workspace()
    project_root = Path(__file__).resolve().parents[2]

    for path_str in paths:
        path = Path(path_str).expanduser()
        if not path.is_absolute():
            # Try workspace first, then project root
            workspace_path = (workspace / path).resolve()
            if workspace_path.exists():
                path = workspace_path
            else:
                project_path = (project_root / path).resolve()
                if project_path.exists():  # noqa: SIM108
                    path = project_path
                else:
                    path = workspace_path  # Default to workspace for error message

        if path.is_file():
            result = read_file(str(path), offset=0, limit=200)
            results.append(f"FILE: {path_str}\n{result}")
        elif path.is_dir():
            result = list_files(str(path))
            results.append(f"DIR: {path_str}\n{result}")
        else:
            results.append(f"NOT FOUND: {path_str}")

    # Build summary context
    context = "\n\n---\n\n".join(results)

    summary = {
        "objective": objective,
        "paths_analyzed": paths,
        "context": context[:50000],  # Cap context size
    }

    return json.dumps(summary)


def _run_command_requires_approval(arguments: dict) -> bool:
    """Check if run_command needs approval."""
    cmd = arguments.get("command", "")
    if not cmd:
        return True
    # Read-only commands don't need approval; everything else does.
    return not is_read_only_command(cmd)


def _scheduler_requires_approval(arguments: dict) -> bool:
    """Scheduler tools are metadata operations - no approval needed."""
    return False


def _proactive_requires_approval(arguments: dict) -> bool:
    """Proactive rules are metadata operations - no approval needed."""
    return False


def _enable_disable_requires_approval(arguments: dict) -> bool:
    """Enable/disable are reversible metadata changes - no approval."""
    return False


def _agent_requires_approval(arguments: dict) -> bool:
    """Agent backends - no approval needed."""
    return False


async def run_opencode(
    objective: str,
    model: str | None = None,
    session_id: str | None = None,
) -> str:
    """Run OpenCode to execute a coding task."""
    from app.agents.factory import get_opencode_backend
    from app.agents.sessions import AgentSessionManager
    from app.config import settings
    from app.persistence.database import Database
    from app.tools.workspace import current_workspace

    workspace = str(current_workspace())
    backend = get_opencode_backend(workspace)
    await backend.start()

    # Get or create session
    db_path = settings.database_path.parent / "employees" / "charlie" / "sage.db"
    # TODO: Get employee key from context - for now use charlie as coder
    db = Database(db_path)
    await db.connect()
    session_mgr = AgentSessionManager(db)

    session = await session_mgr.get_or_create_session(
        employee_key="charlie",
        backend="opencode",
        workspace_path=workspace,
        objective=objective,
        external_session_id=session_id,
    )

    from app.agents.interface import AgentTask
    task = AgentTask(
        objective=objective,
        workspace=workspace,
        model=model,
        session_id=session.external_session_id,
    )
    result = await backend.run_task(task)

    # Update session with external session ID and completion
    if result.session_id:
        await session_mgr.complete_session(
            session.session_id,
            status="completed" if result.success else "failed",
            external_session_id=result.session_id,
        )

    await db.close()

    return json.dumps({
        "success": result.success,
        "output": result.output,
        "error": result.error,
        "session_id": result.session_id,
        "duration_seconds": result.duration_seconds,
    })


async def run_hermes(
    objective: str,
    model: str | None = None,
    session_id: str | None = None,
) -> str:
    """Run Hermes to execute a general-purpose task."""
    from app.agents.factory import get_hermes_backend
    from app.agents.sessions import AgentSessionManager
    from app.config import settings
    from app.persistence.database import Database
    from app.tools.workspace import current_workspace

    workspace = str(current_workspace())
    backend = get_hermes_backend(workspace)
    await backend.start()

    # Get or create session - determine employee from context
    # For now, use a default employee key
    db_path = settings.database_path.parent / "employees" / "alpha" / "sage.db"
    db = Database(db_path)
    await db.connect()
    session_mgr = AgentSessionManager(db)

    session = await session_mgr.get_or_create_session(
        employee_key="alpha",
        backend="hermes",
        workspace_path=workspace,
        objective=objective,
        external_session_id=session_id,
    )

    from app.agents.interface import AgentTask
    task = AgentTask(
        objective=objective,
        workspace=workspace,
        model=model,
        session_id=session.external_session_id,
    )
    result = await backend.run_task(task)

    if result.session_id:
        await session_mgr.complete_session(
            session.session_id,
            status="completed" if result.success else "failed",
            external_session_id=result.session_id,
        )

    await db.close()

    return json.dumps({
        "success": result.success,
        "output": result.output,
        "error": result.error,
        "session_id": result.session_id,
        "duration_seconds": result.duration_seconds,
    })


async def cancel_task(
    task_id: str,
    reason: str = "Cancelled by SAGE",
) -> str:
    """Cancel a running background task. SAGE-only tool."""
    from app.application import get_sage_app

    app = get_sage_app()
    if not app:
        return json.dumps({"success": False, "error": "SAGE not initialized"})

    try:
        await app.supervisor.cancel_task(task_id, reason)
        return json.dumps({"success": True, "task_id": task_id, "reason": reason})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def register_builtin_tools(registry, supervisor=None) -> None:
    registry.register(
        ToolDefinition(
            name="get_current_time",
            description="Get the current local date and time.",
            classification=ToolClassification.READ_ONLY,
            input_schema=CurrentTimeInput,
            destructive=False,
            employee_eligible=False,
            handler=get_current_time,
        )
    )

    registry.register(
        ToolDefinition(
            name="get_current_date",
            description="Get the current local date.",
            classification=ToolClassification.READ_ONLY,
            input_schema=CurrentDateInput,
            destructive=False,
            employee_eligible=False,
            handler=get_current_date,
        )
    )

    # Analyze tool: always available (worker-eligible for background execution)
    registry.register(
        ToolDefinition(
            name="analyze",
            description=(
                "Analyze files and directories to produce a structured summary. "
                "Reads multiple paths (files or directories) using read_file/list_files "
                "and returns consolidated context for the given objective. "
                "Employee-eligible — employees can run this in the background."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=AnalyzeInput,
            destructive=False,
            employee_eligible=True,
            handler=analyze,
        )
    )

    # Agent backend tools (employee-eligible for background execution)
    # Registered regardless of supervisor - available to employees
    registry.register(
        ToolDefinition(
            name="run_opencode",
            description=(
                "Run OpenCode to execute a coding task in the current workspace. "
                "Use for writing, modifying, or analyzing code. "
                "Supports session continuation via session_id. "
                "Employee-eligible — employees can run this in the background."
            ),
            classification=ToolClassification.EXTERNAL_SIDE_EFFECT,
            input_schema=RunOpenCodeInput,
            destructive=False,
            employee_eligible=True,
            requires_approval=_agent_requires_approval,
            handler=run_opencode,
        )
    )

    registry.register(
        ToolDefinition(
            name="run_hermes",
            description=(
                "Run Hermes to execute a general-purpose task in the current workspace. "
                "Use for research, analysis, writing, or any non-coding task. "
                "Supports session continuation via session_id. "
                "Employee-eligible — employees can run this in the background."
            ),
            classification=ToolClassification.EXTERNAL_SIDE_EFFECT,
            input_schema=RunHermesInput,
            destructive=False,
            employee_eligible=True,
            requires_approval=_agent_requires_approval,
            handler=run_hermes,
        )
    )

    # SAGE-only: cancel a running background task
    registry.register(
        ToolDefinition(
            name="cancel_task",
            description=(
                "Cancel a running background task assigned to an employee. "
                "SAGE-only tool — not available to employees."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=CancelTaskInput,
            destructive=True,
            employee_eligible=False,
            handler=cancel_task,
        )
    )

    if supervisor is None:
        return

    async def assign_task(worker: str, objective: str, auto_select: bool = False) -> str:
        """Assign an objective to a named employee; returns a task_id."""
        if auto_select or worker.lower() == "auto":
            return await supervisor.assign_auto(objective)
        return await supervisor.assign(
            worker=worker,
            objective=objective,
        )

    async def task_status(task_id: str) -> dict:
        """Check the status and result of a previously assigned task."""
        task = await supervisor.get_status(task_id)

        if task is None:
            return {"error": f"Unknown task: {task_id}"}

        return {
            "task_id": task["task_id"],
            "objective": task["objective"],
            "status": task["status"],
            "verification": task.get("verification"),
            "verification_detail": task.get("verification_detail"),
            "result": task["result"],
            "error": task["error"],
            "stage": task.get("stage"),
            "last_tool": task.get("last_tool"),
            "iterations": task.get("iterations"),
            "elapsed_seconds": task.get("duration_seconds"),
            "last_activity_at": task.get("last_activity_at"),
            "seconds_since_activity": task.get("seconds_since_activity"),
        }

    registry.register(
        ToolDefinition(
            name="assign_task",
            description=(
                "Assign an objective to a named employee (worker) and get a "
                "task_id back immediately. The employee works in the "
                "background so the conversation stays free. Use this for "
                "substantial work. Check progress with task_status."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=AssignTaskInput,
            destructive=False,
            employee_eligible=False,
            handler=assign_task,
        )
    )

    registry.register(
        ToolDefinition(
            name="task_status",
            description=(
                "Get the current status and result of a task assigned "
                "with assign_task."
            ),
            classification=ToolClassification.READ_ONLY,
            input_schema=TaskStatusInput,
            destructive=False,
            employee_eligible=False,
            handler=task_status,
        )
    )

    # Scheduler tools (metadata operations - no approval needed)
    registry.register(
        ToolDefinition(
            name="schedule_job",
            description=(
                "Create a recurring scheduled job that publishes an event on a cron schedule. "
                "Use standard cron expressions (e.g., '0 * * * *' for hourly)."
            ),
            classification=ToolClassification.READ_ONLY,  # Metadata only
            input_schema=ScheduleJobInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_scheduler_requires_approval,
            handler=None,  # Set by application after scheduler init
        )
    )

    registry.register(
        ToolDefinition(
            name="list_jobs",
            description="List all scheduled jobs with their next run times.",
            classification=ToolClassification.READ_ONLY,
            input_schema=ListJobsInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_scheduler_requires_approval,
            handler=None,  # Set by application after scheduler init
        )
    )

    registry.register(
        ToolDefinition(
            name="enable_job",
            description="Enable or disable a scheduled job.",
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=EnableJobInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_enable_disable_requires_approval,
            handler=None,  # Set by application after scheduler init
        )
    )

    registry.register(
        ToolDefinition(
            name="remove_job",
            description="Remove a scheduled job permanently.",
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=RemoveJobInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_scheduler_requires_approval,
            handler=None,  # Set by application after scheduler init
        )
    )

    registry.register(
        ToolDefinition(
            name="add_proactive_rule",
            description=(
                "Create a proactive rule that watches for events and triggers actions. "
                "Condition is a Python expression with 'payload' and 'event' in scope."
            ),
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=AddProactiveRuleInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_proactive_requires_approval,
            handler=None,  # Set by application after proactive init
        )
    )

    registry.register(
        ToolDefinition(
            name="list_rules",
            description="List all proactive rules.",
            classification=ToolClassification.READ_ONLY,
            input_schema=ListRulesInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_scheduler_requires_approval,
            handler=None,  # Set by application after proactive init
        )
    )

    registry.register(
        ToolDefinition(
            name="enable_rule",
            description="Enable or disable a proactive rule.",
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=EnableRuleInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_enable_disable_requires_approval,
            handler=None,  # Set by application after proactive init
        )
    )

    registry.register(
        ToolDefinition(
            name="remove_rule",
            description="Remove a proactive rule permanently.",
            classification=ToolClassification.LOCAL_MUTATION,
            input_schema=RemoveRuleInput,
            destructive=False,
            employee_eligible=False,
            requires_approval=_proactive_requires_approval,
            handler=None,  # Set by application after proactive init
        )
    )


    # These will be set by the application after supervisor is created
    # (assign_task, task_status handlers need supervisor)
