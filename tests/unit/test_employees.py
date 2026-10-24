"""Tests for the Worker-Employee system (plan Phase 7).

Covers the frozen contract (§2), the roster, budget enforcement, and the
employee memory/workspace isolation. Uses a scripted `FakeInferenceProvider`
in place of a real LLM so an employee's loop runs deterministically.
"""

import asyncio

import pytest

from app.config import settings
from app.employees.budgets import Budget, BudgetExceeded, BudgetTracker
from app.employees.employee import Employee
from app.employees.roster import ROSTER, display_name, normalize
from app.employees.roster import get as get_spec
from app.employees.supervisor import Supervisor
from app.inference.fake import FakeInferenceProvider
from app.inference.manager import InferenceManager
from app.inference.models import InferenceResponse, ModelCapabilities
from app.inference.registry import ModelRegistry
from app.notifications.completion import CompletionNotifier
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import TaskStore
from app.tools.file_tools import register_file_tools
from app.tools.registry import ToolRegistry

# --- roster (N9 + naming decisions) ---


def test_roster_is_fixed_five():
    keys = [spec.key for spec in ROSTER]
    assert keys == ["asta", "otto", "tony", "alpha", "charlie"]
    assert len(ROSTER) == 5


def test_roster_ids_and_display_names():
    assert get_spec("asta").id == "employee-asta"
    assert get_spec("Asta").name == "Asta"
    assert display_name("otto") == "Otto"


def test_roster_normalize_is_case_insensitive_and_accepts_id():
    assert normalize("ASTA") == "asta"
    assert normalize("Employee-Otto") == "otto"
    assert normalize(" charlie ") == "charlie"


def test_roster_unknown_worker_raises():
    with pytest.raises(KeyError):
        normalize("nobody")


# --- budgets (N8) ---


def test_budget_tracker_counts_iterations():
    tracker = BudgetTracker(budget=Budget(max_iterations=2))
    tracker.start_iteration()
    tracker.start_iteration()

    with pytest.raises(BudgetExceeded) as exc:
        tracker.start_iteration()
    assert exc.value.kind == "iterations"


def test_budget_tracker_enforces_token_ceiling():
    tracker = BudgetTracker(budget=Budget(max_tokens=10))
    tracker.add_tokens(5)

    with pytest.raises(BudgetExceeded) as exc:
        tracker.add_tokens(6)
    assert exc.value.kind == "tokens"


def test_budget_tracker_enforces_wall_clock():
    # 0 means unlimited, so should NOT raise
    tracker = BudgetTracker(budget=Budget(max_wall_seconds=0))
    tracker.check_time()  # Should not raise
    
    # But a positive limit should enforce
    tracker2 = BudgetTracker(budget=Budget(max_wall_seconds=0.001))  # 1ms
    import time
    time.sleep(0.01)
    with pytest.raises(BudgetExceeded) as exc:
        tracker2.check_time()
    assert exc.value.kind == "wall_clock"


# --- employee isolation ---


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    # Employee stores live under data/employees/<key>/ next to the DB.
    monkeypatch.setattr(settings, "database_path", tmp_path / "sage.db")
    return tmp_path


def build_supervisor(database, scripted=None, budget=None, registry=None) -> Supervisor:
    provider = FakeInferenceProvider(
        scripted=scripted,
        capabilities=ModelCapabilities(tool_calling=True, streaming=True),
    )
    model_registry = ModelRegistry()
    model_registry.register(provider)
    inference_manager = InferenceManager(model_registry)

    return Supervisor(
        task_store=TaskStore(database),
        permission_engine=PermissionEngine(),
        registry=registry or ToolRegistry(),
        inference_manager=inference_manager,
        model_registry=model_registry,
        default_budget=budget,
    )


async def test_assign_returns_id_and_completes(database):
    supervisor = build_supervisor(
        database,
        scripted=[InferenceResponse(model="fake", content="done: 42")],
    )

    task_id = await supervisor.assign("asta", "compute 42")
    assert task_id.startswith("task_")

    await asyncio.gather(*supervisor._background.values())

    task = await supervisor.get_status(task_id)
    assert task is not None
    # Frozen contract keys (§2) are all present.
    for key in (
        "task_id",
        "objective",
        "worker",
        "status",
        "verification",
        "verification_detail",
        "result",
        "error",
    ):
        assert key in task

    assert task["worker"] == "asta"
    assert task["status"] == "completed"
    assert task["result"] == "done: 42"


async def test_unknown_worker_is_refused(database):
    supervisor = build_supervisor(database)
    with pytest.raises(KeyError):
        await supervisor.assign("nobody", "do something")


async def test_employee_gets_own_workspace_and_memory(database):
    supervisor = build_supervisor(
        database,
        scripted=[InferenceResponse(model="fake", content="ok")],
    )

    task_id = await supervisor.assign("asta", "hello")
    await asyncio.gather(*supervisor._background.values())

    employee = supervisor._employees["asta"]
    assert employee.workspace.exists()
    assert employee.workspace.name == "workspace"
    assert "asta" in str(employee.workspace)

    task = await supervisor.get_status(task_id)
    assert task["status"] == "completed"


async def test_roster_status_reports_standby_then_active(database):
    supervisor = build_supervisor(
        database,
        scripted=[InferenceResponse(model="fake", content="ok")],
    )

    before = {row["worker"]: row["status"] for row in supervisor.roster_status()}
    assert before == {spec.key: "standby" for spec in ROSTER}

    task_id = await supervisor.assign("otto", "do it")
    during = {row["worker"]: row for row in supervisor.roster_status()}
    assert during["otto"]["status"] == "active"
    assert task_id in during["otto"]["running_tasks"]

    await asyncio.gather(*supervisor._background.values())


async def test_activity_lists_worker_assignments(database):
    supervisor = build_supervisor(
        database,
        scripted=[
            InferenceResponse(model="fake", content="one"),
            InferenceResponse(model="fake", content="two"),
        ],
    )

    await supervisor.assign("asta", "first")
    await supervisor.assign("charlie", "second")
    await asyncio.gather(*supervisor._background.values())

    asta_activity = await supervisor.activity("Asta")
    assert len(asta_activity) == 1
    assert asta_activity[0]["objective"] == "first"


async def test_budget_breach_reports_failed_honestly(database):
    # Use a tiny wall-clock limit to force immediate breach
    budget = Budget(max_iterations=0, max_tokens=0, max_wall_seconds=0.001)  # 1ms
    supervisor = build_supervisor(
        database,
        scripted=[InferenceResponse(model="fake", content="never")],
        budget=budget,
    )

    task_id = await supervisor.assign("tony", "too much work")
    await asyncio.gather(*supervisor._background.values())

    task = await supervisor.get_status(task_id)
    assert task["status"] == "failed"
    assert "budget exceeded" in task["error"]


async def test_employee_writes_to_its_own_workspace(database):
    """File tools must resolve into the employee's workspace, not SAGE's."""

    tools = ToolRegistry()
    register_file_tools(tools)

    supervisor = build_supervisor(
        database,
        registry=tools,
        scripted=[
            InferenceResponse(
                model="fake",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path": "notes.md", "content": "hi"}',
                        },
                    }
                ],
            ),
            InferenceResponse(model="fake", content="wrote notes.md"),
        ],
    )

    task_id = await supervisor.assign("asta", "write notes.md")
    await asyncio.gather(*supervisor._background.values())

    task = await supervisor.get_status(task_id)
    assert task["status"] == "completed"

    employee = supervisor._employees["asta"]
    assert (employee.workspace / "notes.md").read_text() == "hi"
    # And nothing leaked into SAGE's global workspace.
    assert not (settings.workspace_path / "notes.md").exists()


async def test_file_tools_are_employee_eligible():
    tools = ToolRegistry()
    register_file_tools(tools)
    for name in ("read_file", "list_files", "write_file", "patch"):
        assert tools.get(name).employee_eligible is True


def test_default_budget_has_unlimited_iterations_and_tokens():
    # Tool calls are cheap: iterations and tokens are unlimited by default;
    # wall-clock is the sole runaway guard.
    budget = Budget()
    assert budget.max_iterations == 0
    assert budget.max_tokens == 0
    assert budget.max_wall_seconds == 600.0

    tracker = BudgetTracker(budget=budget)
    for _ in range(50):
        tracker.start_iteration()
    tracker.add_tokens(10_000_000)
    tracker.check_time()


async def test_recover_marks_stuck_assignments_unknown(database):
    supervisor = build_supervisor(database)
    store = TaskStore(database)

    stuck = await store.create(
        objective="left running", tool_name="employee:asta", arguments={},
        worker="asta",
    )

    recovered = await supervisor.recover()
    assert recovered >= 1

    task = await supervisor.get_status(stuck)
    assert task["status"] == "unknown"


async def test_task_status_surfaces_progress_fields(database):
    tools = ToolRegistry()
    register_file_tools(tools)
    supervisor = build_supervisor(
        database,
        registry=tools,
        scripted=[
            InferenceResponse(
                model="fake",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path": "a.md", "content": "1"}',
                        },
                    }
                ],
            ),
            InferenceResponse(model="fake", content="done"),
        ],
    )

    captured: dict = {}

    # Observe progress mid-run by wrapping the progress callback the
    # supervisor hands the employee.
    original = supervisor._make_progress

    def _spy(task_id, spec):
        cb = original(task_id, spec)

        async def _wrapped(event):
            await cb(event)
            status = await supervisor.get_status(task_id)
            captured.update(status or {})

        return _wrapped

    supervisor._make_progress = _spy  # type: ignore[method-assign]

    task_id = await supervisor.assign("asta", "write a.md")
    await asyncio.gather(*supervisor._background.values())

    # Mid-run snapshot carried live progress.
    assert captured["stage"] in ("llm_start", "tool_start", "tool_end")
    assert captured["last_tool"] == "write_file"
    assert captured["iterations"] >= 1
    assert captured["seconds_since_activity"] is not None

    # After completion the task is terminal and progress is cleared.
    final = await supervisor.get_status(task_id)
    assert final["status"] == "completed"
    assert final["stage"] is None


# --- Honest verification of employee results ---


def test_every_roster_member_has_a_role_card():
    from app.employees.factory import _load_role_card

    for spec in ROSTER:
        card = _load_role_card(spec.key)
        # A bespoke card exists (not the generic fallback).
        assert "You are an employee working under SAGE." not in card
        assert spec.name in card
        assert spec.role_card == f"{spec.key}.md"


async def test_completion_notifier_queues_and_drains_fifo():
    from app.events.models import Event, EventTypes

    notifier = CompletionNotifier()

    await notifier.handle_event(
        Event(
            event_type=EventTypes.EMPLOYEE_FINISHED,
            source="employee",
            payload={"task_id": "task_1", "name": "Asta", "summary": "did it"},
        )
    )
    await notifier.handle_event(
        Event(
            event_type=EventTypes.TASK_FAILED,
            source="employee",
            payload={"task_id": "task_2", "name": "Otto", "error": "boom"},
        )
    )
    # Irrelevant events are ignored.
    await notifier.handle_event(
        Event(event_type="user_message", source="cli", payload={})
    )

    notices = notifier.drain()
    assert len(notices) == 2
    assert "Asta" in notices[0] and "task_1" in notices[0]
    assert "Otto" in notices[1] and "boom" in notices[1]
    assert notifier.drain() == []


async def test_read_only_task_reports_unverified(database):
    supervisor = build_supervisor(
        database,
        scripted=[InferenceResponse(model="fake", content="all done")],
    )

    task_id = await supervisor.assign("asta", "just think about it")
    await asyncio.gather(*supervisor._background.values())

    task = await supervisor.get_status(task_id)
    assert task["status"] == "completed"
    assert task["verification"] == "unverified"
    assert "no side-effecting tool" in task["verification_detail"]


async def test_side_effecting_task_reports_self_verified(database):
    tools = ToolRegistry()
    register_file_tools(tools)
    supervisor = build_supervisor(
        database,
        registry=tools,
        scripted=[
            InferenceResponse(
                model="fake",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path": "out.md", "content": "x"}',
                        },
                    }
                ],
            ),
            InferenceResponse(model="fake", content="wrote the file"),
        ],
    )

    task_id = await supervisor.assign("asta", "write out.md")
    await asyncio.gather(*supervisor._background.values())

    task = await supervisor.get_status(task_id)
    assert task["status"] == "completed"
    assert task["verification"] == "self-verified"
    assert "write_file" in task["verification_detail"]


# --- Employee dataclass surface ---
def test_employee_exposes_id_and_name():
    spec = get_spec("alpha")
    employee = Employee(
        spec=spec,
        workspace=None,  # not used by the property assertions
        loop=None,
    )
    assert employee.id == "employee-alpha"
    assert employee.name == "Alpha"


# --- Employee uses agent tools for coding tasks ---

async def test_charlie_uses_run_opencode_for_coding_task(database):
    """Verify that Charlie (coder) calls run_opencode when given a coding objective."""
    from app.tools.builtin import register_builtin_tools

    tools = ToolRegistry()
    register_builtin_tools(tools)

    # Script: first call run_opencode, then return completion
    scripted = [
        InferenceResponse(
            model="fake",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "run_opencode",
                        "arguments": '{"objective": "Build a Next.js mobile menu component", "model": null, "session_id": null}',
                    },
                }
            ],
        ),
        InferenceResponse(model="fake", content="Created mobile menu component in next-app"),
    ]

    supervisor = build_supervisor(
        database,
        registry=tools,
        scripted=scripted,
    )

    task_id = await supervisor.assign("charlie", "Build a Next.js mobile menu component")
    await asyncio.gather(*supervisor._background.values())

    task = await supervisor.get_status(task_id)
    assert task["status"] == "completed"
    # The employee should have called run_opencode (side-effecting tool)
    assert task["verification"] == "self-verified"
    assert "run_opencode" in task["verification_detail"]


async def test_alpha_uses_run_hermes_for_research_task(database):
    """Verify that Alpha (researcher) calls run_hermes when given a research objective."""
    from app.tools.builtin import register_builtin_tools

    tools = ToolRegistry()
    register_builtin_tools(tools)

    scripted = [
        InferenceResponse(
            model="fake",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "run_hermes",
                        "arguments": '{"objective": "Research React Server Components best practices", "model": null, "session_id": null}',
                    },
                }
            ],
        ),
        InferenceResponse(model="fake", content="Research complete: RSC best practices documented"),
    ]

    supervisor = build_supervisor(
        database,
        registry=tools,
        scripted=scripted,
    )

    task_id = await supervisor.assign("alpha", "Research React Server Components best practices")
    await asyncio.gather(*supervisor._background.values())

    task = await supervisor.get_status(task_id)
    assert task["status"] == "completed"
    assert task["verification"] == "self-verified"
    assert "run_hermes" in task["verification_detail"]


async def test_employee_system_prompt_includes_tools_guide(database):
    """Verify employee's system prompt includes the tools guide."""
    from app.tools.builtin import register_builtin_tools

    tools = ToolRegistry()
    register_builtin_tools(tools)

    supervisor = build_supervisor(
        database,
        registry=tools,
        scripted=[InferenceResponse(model="fake", content="ok")],
    )

    task_id = await supervisor.assign("charlie", "test")
    await asyncio.gather(*supervisor._background.values())

    employee = supervisor._employees["charlie"]
    # The system prompt should contain both role card and tools guide
    system_prompt = employee.loop.context_builder.system_prompt
    assert "Charlie" in system_prompt
    assert "run_opencode" in system_prompt
    assert "run_hermes" in system_prompt
    assert "Agent Backends" in system_prompt
