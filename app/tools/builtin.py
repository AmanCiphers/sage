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
    
    Uses read_file and list_files internally. Worker-eligible for background execution.
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

    if supervisor is None:
        return

    async def assign_task(worker: str, objective: str) -> str:
        """Assign an objective to a named employee; returns a task_id."""
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
