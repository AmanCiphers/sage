"""OpenCode agent backend implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from app.agents.interface import AgentBackend, AgentEvent, AgentResult, AgentTask

logger = logging.getLogger(__name__)


class OpenCodeBackend(AgentBackend):
    """OpenCode CLI backend for coding tasks."""

    name = "opencode"

    def __init__(
        self,
        workspace: str,
        opencode_path: str = "opencode",
        default_model: str = "nebius/deepseek-ai/DeepSeek-V4-Flash-0731",
        env: dict[str, str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.opencode_path = opencode_path
        self.default_model = default_model
        self.env = env or os.environ.copy()
        self._process: asyncio.subprocess.Process | None = None
        self._current_session_id: str | None = None

    async def start(self) -> None:
        """Verify OpenCode is installed and accessible."""
        if not shutil.which(self.opencode_path):
            raise RuntimeError(
                f"OpenCode not found at '{self.opencode_path}'. "
                "Install with: npm install -g opencode-ai"
            )
        logger.info("OpenCode backend ready at %s", self.opencode_path)

    async def _run_opencode(
        self,
        prompt: str,
        model: str | None = None,
        session_id: str | None = None,
        continue_last: bool = False,
        stream_events: AsyncIterator[AgentEvent] | None = None,
    ) -> tuple[str, str | None]:
        """Run opencode command and return (output, session_id).
        
        If stream_events is provided, yields AgentEvents during execution.
        """
        cmd = [self.opencode_path, "run"]

        if model:
            cmd.extend(["--model", model])
        else:
            cmd.extend(["--model", self.default_model])

        if continue_last:
            cmd.append("--continue")
        elif session_id:
            cmd.extend(["--session", session_id])

        cmd.extend(["--dir", str(self.workspace)])
        cmd.append(prompt)

        logger.debug("Running OpenCode: %s", " ".join(cmd))

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.workspace),
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        output_lines = []
        session_id = None

        # Stream stdout and stderr
        async def read_stream(stream, is_stderr=False):
            nonlocal session_id
            while True:
                line = await stream.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").rstrip("\n")
                if is_stderr:
                    logger.debug("OpenCode stderr: %s", line_str)
                else:
                    output_lines.append(line_str)
                    # Extract session ID from output if present
                    if session_id is None:
                        extracted = self._extract_session_id(line_str)
                        if extracted:
                            session_id = extracted
                    # Yield progress event if streaming
                    if stream_events is not None:
                        yield AgentEvent(
                            type="progress",
                            message=line_str[:200],
                            data={"source": "stdout"}
                        )

        # Read both streams concurrently
        stdout_task = asyncio.create_task(read_stream(self._process.stdout))
        stderr_task = asyncio.create_task(read_stream(self._process.stderr, is_stderr=True))

        await asyncio.gather(stdout_task, stderr_task)
        await self._process.wait()

        output = "\n".join(output_lines)
        error_output = ""  # stderr handled separately

        if self._process.returncode != 0:
            logger.warning("OpenCode exited with code %d", self._process.returncode)

        return "\n".join(output_lines), session_id

    def _extract_session_id(self, output: str) -> str | None:
        """Extract OpenCode session ID from output."""
        # OpenCode prints session info; look for session ID pattern
        # Format varies, try common patterns
        patterns = [
            r"session[:\s]+([a-zA-Z0-9_-]{10,})",
            r"Session[:\s]+([a-zA-Z0-9_-]{10,})",
            r"session_id[\"']?\s*:\s*[\"']([a-zA-Z0-9_-]{10,})[\"']",
        ]
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    async def run_task(self, task: AgentTask) -> AgentResult:
        """Execute a coding task via OpenCode."""
        import time
        start = time.monotonic()

        # Determine if we should continue a session
        continue_last = task.session_id is None and self._current_session_id is not None
        session_id = task.session_id or self._current_session_id

        # Build prompt with context
        prompt = self._build_prompt(task)

        output, new_session_id = await self._run_opencode(
            prompt=prompt,
            model=task.model,
            session_id=session_id,
            continue_last=continue_last,
        )

        if new_session_id:
            self._current_session_id = new_session_id

        duration = time.monotonic() - start
        success = self._process is not None and self._process.returncode == 0

        return AgentResult(
            success=success,
            output=output.strip(),
            error=output.strip() if not success else None,
            duration_seconds=duration,
            session_id=self._current_session_id,
        )

    def _build_prompt(self, task: AgentTask) -> str:
        """Build the prompt for OpenCode with context."""
        parts = [task.objective]

        if task.context_files:
            parts.append("\n\nRelevant files:")
            for f in task.context_files:
                parts.append(f"- {f}")

        if task.context_summary:
            parts.append(f"\n\nContext summary:\n{task.context_summary}")

        parts.append("\n\nWork in the current directory. Make changes directly to files.")
        return "".join(parts)

    async def cancel(self) -> None:
        """Cancel running OpenCode process."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            logger.info("OpenCode process cancelled")

    async def get_session_id(self) -> str | None:
        return self._current_session_id

    async def stream_events(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        """Stream events during OpenCode execution with real-time output."""
        import time
        start = time.monotonic()

        continue_last = task.session_id is None and self._current_session_id is not None
        session_id = task.session_id or self._current_session_id
        prompt = self._build_prompt(task)

        yield AgentEvent(type="start", message="Starting OpenCode task...")

        cmd = [self.opencode_path, "run"]

        if task.model:
            cmd.extend(["--model", task.model])
        else:
            cmd.extend(["--model", self.default_model])

        if continue_last:
            cmd.append("--continue")
        elif session_id:
            cmd.extend(["--session", session_id])

        cmd.extend(["--dir", str(self.workspace)])
        cmd.append(prompt)

        logger.debug("Running OpenCode: %s", " ".join(cmd))

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.workspace),
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        output_lines = []
        new_session_id = None

        # Stream stdout
        async for line in self._process.stdout:
            line_str = line.decode("utf-8", errors="replace").rstrip("\n")
            yield AgentEvent(
                type="progress",
                message=line_str[:500],
                data={"source": "stdout", "line": line_str}
            )
            output_lines.append(line_str)

        # Read stderr silently
        async for _ in self._process.stderr:
            pass

        await self._process.wait()

        output = "\n".join(output_lines)
        duration = time.monotonic() - start
        success = self._process is not None and self._process.returncode == 0

        if self._current_session_id:
            new_session_id = self._current_session_id

        if success:
            yield AgentEvent(
                type="complete",
                message="Task completed",
                data={"result": output.strip(), "duration_seconds": duration},
            )
        else:
            yield AgentEvent(type="error", message="Task failed")
