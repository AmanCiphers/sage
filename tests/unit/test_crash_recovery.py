"""Process-level crash recovery.

The existing recovery tests call ``recover()`` directly in the same process.
That proves the SQL is right, not that a crashed process leaves recoverable
state. These tests write durable rows in a child process, kill it with
SIGKILL (no cleanup, no flush, no shutdown hook), then reopen the database in
the test process and assert recovery behaves.
"""

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from app.execution.engine import ExecutionEngine
from app.operations.manager import OperationManager
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import TaskStore
from app.tools.registry import ToolRegistry

CHILD = """
import asyncio, json, os, signal, sys
from pathlib import Path

from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import TaskStore


async def main(db_path: str) -> None:
    db = Database(Path(db_path))
    await db.connect()
    await initialize_schema(db)
    await db.commit()

    await TaskStore(db).create(
        objective="half-finished work",
        tool_name="employee:asta",
        arguments={},
        worker="asta",
    )

    # The row OperationManager.execute() writes before running the tool.
    await db.execute(
        "INSERT INTO operations (operation_id, approval_id, tool_name, arguments_json, status)"
        " VALUES ('op_test', NULL, 'write_file', ?, 'EXECUTING')",
        (json.dumps({"path": "x.py"}),),
    )
    await db.commit()

    # Die without unwinding: no close(), no recover().
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)


asyncio.run(main(sys.argv[1]))
"""


@pytest.fixture
def killed_db(tmp_path: Path) -> Path:
    """Run the child to completion-by-SIGKILL and return the database path."""
    db_path = tmp_path / "crashed.db"
    script = tmp_path / "child.py"
    script.write_text(CHILD)

    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    proc = subprocess.run(
        [sys.executable, str(script), str(db_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert "READY" in proc.stdout, f"child did not reach its crash point: {proc.stderr}"
    assert proc.returncode == -signal.SIGKILL, "child was expected to die by SIGKILL"
    assert db_path.exists()
    return db_path


async def test_crashed_process_leaves_recoverable_state(killed_db: Path):
    """After an unclean kill, the database opens and both recovery paths fire:
    an in-flight operation becomes UNKNOWN and the in-flight task is reconciled."""
    db = Database(killed_db)
    await db.connect()
    try:
        registry = ToolRegistry()
        operations = OperationManager(db, ExecutionEngine(registry), registry)
        assert await operations.recover() == 1

        cursor = await db.execute("SELECT status FROM operations")
        assert [row[0] for row in await cursor.fetchall()] == ["UNKNOWN"]

        assert await TaskStore(db).recover_stuck() == 1
        cursor = await db.execute("SELECT status FROM tasks")
        assert [row[0] for row in await cursor.fetchall()] == ["unknown"]
    finally:
        await db.close()


async def test_committed_history_survives_an_unclean_kill(killed_db: Path):
    """WAL mode means committed rows are intact even though the writer was
    killed before closing the connection."""
    db = Database(killed_db)
    await db.connect()
    try:
        cursor = await db.execute("SELECT objective FROM tasks")
        rows = await cursor.fetchall()
        assert rows == [("half-finished work",)]
    finally:
        await db.close()
