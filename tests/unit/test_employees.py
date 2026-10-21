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
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import TaskStore
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
    tracker = BudgetTracker(budget=Budget(max_wall_seconds=0))
    with pytest.raises(BudgetExceeded) as exc:
        tracker.check_time()
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


def build_supervisor(database, scripted=None, budget=None) -> Supervisor:
    provider = FakeInferenceProvider(
        scripted=scripted,
        capabilities=ModelCapabilities(tool_calling=True, streaming=True),
    )
    registry = ModelRegistry()
    registry.register(provider)
    inference_manager = InferenceManager(registry)

    return Supervisor(
        task_store=TaskStore(database),
        permission_engine=PermissionEngine(),
        registry=ToolRegistry(),
        inference_manager=inference_manager,
        model_registry=registry,
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
    # Budget allows zero iterations → the first loop turn breaches it.
    budget = Budget(max_iterations=0, max_tokens=0, max_wall_seconds=0)
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
