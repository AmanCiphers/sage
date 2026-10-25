"""Hermes agent backend implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from app.agents.interface import AgentBackend, AgentEvent, AgentResult, AgentTask

logger = logging.getLogger(__name__)


class HermesBackend(AgentBackend):
    """Hermes CLI backend for general-purpose tasks."""

    name = "hermes"

    def __init__(
        self,
        workspace: str,
        hermes_path: str = "hermes",
        default_model: str = "openai/gpt-4",
        env: dict[str, str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.hermes_path = hermes_path
        self.default_model = default_model
        self.env = env or os.environ.copy()
        self._process: asyncio.subprocess.Process | None = None
        self._current_session_id: str | None = None

    async def start(self) -> None:
        """Verify Hermes is installed and accessible."""
        if not shutil.which(self.hermes_path):
            raise RuntimeError(
                f"Hermes not found at '{self.hermes_path}'. "
                "Install from: https://github.com/NousResearch/hermes-agent"
            )
        logger.info("Hermes backend ready at %s", self.hermes_path)

    async def _run_hermes(
        self,
        prompt: str,
        model: str | None = None,
        session_id: str | None = None,
        continue_last: bool = False,
    ) -> tuple[str, str | None]:
        """Run hermes command and return (output, session_id).

        Hermes v0.20.x: one-shot prompts use the top-level `-z PROMPT` flag
        (NOT `hermes chat ... --format stream-json`, which does not exist).
        `--pass-session-id` makes the session id available on stdout so we
        can resume across assignments.
        """
        cmd = [self.hermes_path, "-z", prompt]

        # Only override the model if the caller explicitly asked for one;
        # otherwise let Hermes use its own configured default (e.g. NVIDIA NIM).
        if model:
            cmd.extend(["--model", model])

        if continue_last:
            cmd.append("--continue")
        elif session_id:
            cmd.extend(["--resume", session_id])

        cmd.extend(["--in", str(self.workspace)])
        cmd.append("--pass-session-id")

        logger.debug("Running Hermes: %s", " ".join(cmd))

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.workspace),
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        output_lines = []
        session_id = None

        # Stream stdout
        async for line in self._process.stdout:
            line_str = line.decode("utf-8", errors="replace").rstrip("\n")
            output_lines.append(line_str)

            # Extract session ID from output if present
            extracted = self._extract_session_id(line_str)
            if extracted:
                session_id = extracted

        # Read stderr silently
        async for _ in self._process.stderr:
            pass

        await self._process.wait()

        output = "\n".join(output_lines)

        if self._process.returncode != 0:
            logger.warning("Hermes exited with code %d: %s", self._process.returncode, error)

        return "\n".join(output_lines), session_id

    def _extract_session_id(self, output: str) -> str | None:
        """Extract Hermes session ID from --pass-session-id output."""
        # Hermes prints a trailing line like:  Session ID: <id>  (or a
        # `session_id=<id>` fragment) when --pass-session-id is set.
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            for marker in ("session id:", "session_id:", "session_id=", "session id="):
                if marker in low:
                    # Take the value after the marker, trimmed of quotes/spaces
                    value = line.split(":", 1)[-1] if ":" in line else line.split("=", 1)[-1]
                    value = value.strip().strip('"').strip("'")
                    if value and not value.lower().startswith(("session", "resume")):
                        return value
            # JSONL fallback (in case a config emits metadata JSON)
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    if data.get("session_id"):
                        return str(data["session_id"])
                    meta = data.get("meta") or data.get("session") or {}
                    if isinstance(meta, dict) and meta.get("session_id"):
                        return str(meta["session_id"])
            except json.JSONDecodeError:
                continue
        return None

    async def run_task(self, task: AgentTask) -> AgentResult:
        """Execute a task via Hermes."""
        import time
        start = time.monotonic()

        continue_last = task.session_id is None and self._current_session_id is not None
        session_id = task.session_id or self._current_session_id

        prompt = self._build_prompt(task)

        output, new_session_id = await self._run_hermes(
            prompt=prompt,
            model=task.model,
            session_id=session_id,
            continue_last=continue_last,
        )

        if new_session_id:
            self._current_session_id = new_session_id

        duration = time.monotonic() - start
        success = self._process is not None and self._process.returncode == 0

        # Clean output: drop the trailing session-id marker line if present.
        parsed_output = self._strip_session_line(output)

        return AgentResult(
            success=success,
            output=parsed_output,
            error=output.strip() if not success else None,
            duration_seconds=duration,
            session_id=self._current_session_id,
        )

    def _strip_session_line(self, output: str) -> str:
        """Remove Hermes' trailing 'Session ID: ...' marker from the reply."""
        lines = [ln for ln in output.splitlines() if ln.strip()]
        if lines:
            low = lines[-1].lower()
            if any(m in low for m in ("session id:", "session_id:", "session_id=")):
                lines.pop()
        return "\n".join(lines).strip()

    def _build_prompt(self, task: AgentTask) -> str:
        """Build the prompt for Hermes with context."""
        parts = [task.objective]

        if task.context_files:
            parts.append("\n\nRelevant files:")
            for f in task.context_files:
                parts.append(f"- {f}")

        if task.context_summary:
            parts.append(f"\n\nContext summary:\n{task.context_summary}")

        parts.append("\n\nWork in the current directory.")
        return "".join(parts)

    async def cancel(self) -> None:
        """Cancel running Hermes process."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            logger.info("Hermes process cancelled")

    async def get_session_id(self) -> str | None:
        return self._current_session_id

    async def stream_events(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        """Stream events during Hermes execution with real-time output."""
        import time
        start = time.monotonic()

        continue_last = task.session_id is None and self._current_session_id is not None
        session_id = task.session_id or self._current_session_id
        prompt = self._build_prompt(task)

        yield AgentEvent(type="start", message="Starting Hermes task...")

        cmd = [self.hermes_path, "-z", prompt]

        if task.model:
            cmd.extend(["--model", task.model])
        else:
            cmd.extend(["--model", self.default_model])

        if continue_last:
            cmd.append("--continue")
        elif session_id:
            cmd.extend(["--resume", session_id])

        cmd.extend(["--in", str(self.workspace)])
        cmd.append("--pass-session-id")

        logger.debug("Running Hermes: %s", " ".join(cmd))

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.workspace),
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        output_lines = []
        new_session_id = None

        yield AgentEvent(type="start", message="Starting Hermes task...")

        # Stream stdout
        async for line in self._process.stdout:
            line_str = line.decode("utf-8", errors="replace").rstrip("\n")
            yield AgentEvent(
                type="progress",
                message=line_str[:500],
                data={"source": "stdout", "line": line_str}
            )
            output_lines.append(line_str)

            # Extract session ID
            extracted = self._extract_session_id(line_str)
            if extracted:
                self._current_session_id = extracted

        # Read stderr silently
        async for _ in self._process.stderr:
            pass

        await self._process.wait()

        output = "\n".join(output_lines)
        duration = time.monotonic() - start
        success = self._process is not None and self._process.returncode == 0

        # Clean output: drop the trailing session-id marker line if present.
        parsed_output = self._strip_session_line(output)

        if success:
            yield AgentEvent(
                type="complete",
                message="Task completed",
                data={"result": parsed_output, "duration_seconds": duration},
            )
        else:
            yield AgentEvent(type="error", message="Task failed")
