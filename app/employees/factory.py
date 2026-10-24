"""Builds an isolated Employee runtime (plan Phase 2).

Each employee gets its **own** world:

    data/employees/<key>/
        sage.db        — its own messages / session / tasks
        memory.md      — its own curated facts
        workspace/     — its own cwd for file & shell tools
        sage.log       — its own log file

The filtered tool registry is the existing SAGE registry, restricted to
tools marked `employee_eligible`. The employee reuses the same
`ConversationLoop`, `PermissionEngine`, and inference as SAGE — only the
*state* and *workspace* are separate.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.context.builder import ContextBuilder
from app.context.compressor import ContextCompressor
from app.conversation.loop import ConversationLoop
from app.embeddings import EmbeddingStore, create_primary_provider
from app.employees.budgets import Budget
from app.employees.employee import Employee
from app.employees.roster import EmployeeSpec
from app.employees.roster import get as get_spec
from app.execution.engine import ExecutionEngine
from app.inference.manager import InferenceManager
from app.inference.models import ModelRequirements
from app.inference.router import ModelRouter
from app.memory.store import MemoryStore
from app.permissions.approvals import ApprovalManager
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import MessageStore, SessionStateStore
from app.tools.registry import ToolRegistry

ROLE_CARDS_DIR = Path(__file__).parent / "roles"

# Absolute backstop for an employee's inner ConversationLoop when the
# assignment budget leaves iterations unlimited. High enough that a normal
# objective never hits it; the wall-clock budget is the real guard.
_LOOP_HARD_CEILING = 500


def employee_root(key: str) -> Path:
    return Path(settings.database_path).parent / "employees" / key


# Per-role tool allowlists. Every employee starts with the employee-eligible
# common set, then each role gets a tailored subset so it only sees the tools
# relevant to its mandate (a coding employee shouldn't be handed the same
# surface as a research employee).
_COMMON_EMPLOYEE_TOOLS = {
    "read_file",
    "list_files",
    "grep",
    "glob",
    "analyze",
    "session_search",
    "memory",
    "run_hermes",
    "web_search",
    "browse_url",
}

_ROLE_ALLOWLIST: dict[str, set[str]] = {
    "charlie": _COMMON_EMPLOYEE_TOOLS | {"run_opencode", "run_command", "write_file", "patch"},
    "alpha": _COMMON_EMPLOYEE_TOOLS,
    "asta": _COMMON_EMPLOYEE_TOOLS,
    "otto": _COMMON_EMPLOYEE_TOOLS | {"write_file", "patch"},
    "tony": _COMMON_EMPLOYEE_TOOLS | {"run_command"},
}


def _filter_registry(source: ToolRegistry, key: str | None = None) -> ToolRegistry:
    """A registry containing only the tools allowed for an employee role.

    Tools are shared by reference (same handlers); the *set* is what differs
    per employee. If `key` is None or the role has no allowlist, fall back to
    all employee-eligible tools.
    """

    allowed = _ROLE_ALLOWLIST.get(key) if key else None

    filtered = ToolRegistry()
    for tool in source.list():
        if not getattr(tool, "employee_eligible", False):
            continue
        if allowed is not None and tool.name not in allowed:
            continue
        filtered.register(tool)
    return filtered


def _load_role_card(key: str) -> str:
    path = ROLE_CARDS_DIR / f"{key}.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    # Fallback role card so an employee without a bespoke card still works.
    return (
        "You are an employee working under SAGE. Complete the assigned "
        "objective using your tools, then return a concise result. Do not "
        "address the user directly."
    )


def _load_tools_guide() -> str:
    """Load the shared TOOLS.md guide for employee system prompts."""
    path = Path(__file__).parent.parent / "conversation" / "tools.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _setup_employee_logger(key: str) -> logging.Logger:
    """Create a per-employee logger with its own file handler."""
    log_path = employee_root(key) / "sage.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"sage.employee.{key}")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if called multiple times
    if not logger.handlers:
        handler = logging.FileHandler(log_path)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False  # Don't bubble up to root logger

    return logger


async def build_employee(
    worker: str | EmployeeSpec,
    *,
    inference_manager: InferenceManager,
    model_registry,
    source_registry: ToolRegistry,
    permission_engine: PermissionEngine,
    budget: Budget | None = None,
) -> Employee:
    """Construct a fully isolated Employee.

    The employee's `Database` is opened and schema-initialized here; the
    caller (Supervisor) owns its lifecycle and should `close_employee`
    when done. In practice employees stay constructed for the session.
    """

    spec = worker if isinstance(worker, EmployeeSpec) else get_spec(worker)

    root = employee_root(spec.key)
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    # Set up per-employee logger
    emp_logger = _setup_employee_logger(spec.key)
    emp_logger.info("employee_initialized key=%s workspace=%s", spec.key, workspace)

    database = Database(root / "sage.db")
    await database.connect()
    await initialize_schema(database)

    emp_embedding_provider = create_primary_provider()
    emp_embedding_store = (
        EmbeddingStore(database, emp_embedding_provider)
        if emp_embedding_provider
        else None
    )

    message_store = MessageStore(
        database,
        embedding_provider=emp_embedding_provider,
        embedding_store=emp_embedding_store,
    )
    session_state = SessionStateStore(database)
    memory_store = MemoryStore(root / "memory.md")

    registry = _filter_registry(source_registry, spec.key)
    execution_engine = ExecutionEngine(registry)
    approval_manager = ApprovalManager(database)

    # Employees need a model with reasoning to follow complex instructions
    router = ModelRouter(model_registry)
    primary = router.route(ModelRequirements(tool_calling=True, reasoning=True))
    compressor = ContextCompressor(
        inference_manager=inference_manager,
        session_state=session_state,
        context_window=primary.metadata.context_window,
        threshold_ratio=settings.compaction_threshold_ratio,
    )

    role_card = _load_role_card(spec.key)
    tools_guide = _load_tools_guide()
    system_prompt = role_card + "\n\n---\n\n" + tools_guide if tools_guide else role_card

    context_builder = ContextBuilder(
        message_store=message_store,
        session_state=session_state,
        compressor=compressor,
        system_prompt=system_prompt,
        history_fetch_limit=settings.history_fetch_limit,
        memory_store=memory_store,
        embedding_store=emp_embedding_store,
        embedding_provider=emp_embedding_provider,
    )

    assignment_budget = budget or Budget()

    loop = ConversationLoop(
        inference_manager=inference_manager,
        tool_registry=registry,
        permission_engine=permission_engine,
        execution_engine=execution_engine,
        approval_manager=approval_manager,
        message_store=message_store,
        context_builder=context_builder,
        compressor=compressor,
        # Follow the assignment budget: 0 means effectively unlimited turns.
        # The per-employee ConversationLoop is just the last-resort backstop.
        max_iterations=(
            assignment_budget.max_iterations
            if assignment_budget.max_iterations
            else _LOOP_HARD_CEILING
        ),
        max_tool_result_chars=settings.max_tool_result_chars,
        # Employees need reasoning to follow complex instructions (use run_opencode, etc.)
        model_requirements=ModelRequirements(tool_calling=True, reasoning=True),
    )

    # Keep a handle so the supervisor can close the DB later.
    employee = Employee(
        spec=spec,
        workspace=workspace,
        loop=loop,
        budget=assignment_budget,
    )
    employee._database = database
    employee._embedding_provider = emp_embedding_provider
    employee._logger = emp_logger
    return employee


async def close_employee(employee: Employee) -> None:
    database = getattr(employee, "_database", None)
    if database is not None:
        await database.close()

    emb_provider = getattr(employee, "_embedding_provider", None)
    if emb_provider:
        await emb_provider.close()

    # Close logger handlers
    emp_logger = getattr(employee, "_logger", None)
    if emp_logger:
        for handler in emp_logger.handlers[:]:
            handler.close()
            emp_logger.removeHandler(handler)
