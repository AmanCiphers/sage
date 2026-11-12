import asyncio
import os
import stat
import sys
from pathlib import Path

from app.agents.hermes import HermesBackend

# One write of 10 MB to stderr, far beyond the pipe buffer, issued *before*
# any stdout. A drain that reads stdout to completion first blocks forever
# waiting for stdout that the child cannot reach the write to until stderr
# is consumed.
_DEADLOCK_STUB = """#!{python}
import sys
sys.stderr.write("x" * 10_000_000)
sys.stderr.flush()
sys.stdout.write("final answer\\n")
sys.stdout.write("Session ID: sess-123\\n")
sys.stdout.flush()
sys.exit(1)
"""

# A single stderr line larger than asyncio's default 64 KiB reader limit.
_LONG_LINE_STUB = """#!{python}
import sys
sys.stderr.write("x" * 200000 + "\\n")
sys.stderr.flush()
sys.stdout.write("ok\\n")
sys.stdout.flush()
sys.exit(0)
"""


def _make_stub(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-hermes"
    script.write_text(body.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _backend(tmp_path: Path, stub_body: str) -> HermesBackend:
    return HermesBackend(
        workspace=str(tmp_path),
        hermes_path=str(_make_stub(tmp_path, stub_body)),
        env=os.environ.copy(),
    )


async def test_hermes_does_not_deadlock_when_stderr_overflows_the_pipe(tmp_path):
    """Regression: stdout was drained before stderr, so a child that filled
    the stderr pipe blocked forever and the turn hung."""
    backend = _backend(tmp_path, _DEADLOCK_STUB)

    output, session_id = await asyncio.wait_for(
        backend._run_hermes(prompt="do the thing"), timeout=10
    )

    assert "final answer" in output
    assert session_id == "sess-123"


async def test_hermes_survives_a_single_overlong_stderr_line(tmp_path):
    """Regression: a stderr line above asyncio's 64 KiB reader limit raised
    an exception instead of being logged."""
    backend = _backend(tmp_path, _LONG_LINE_STUB)

    output, _ = await asyncio.wait_for(backend._run_hermes(prompt="x"), timeout=10)

    assert output.strip() == "ok"
