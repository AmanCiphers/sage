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
        """Run hermes command and return (output, session_id)."""
        # Hermes uses `hermes chat` for interactive/one-shot
        cmd = [self.hermes_path, "chat"]

        if model:
            cmd.extend(["--provider", "openrouter", "--model", model])
        else:
            cmd.extend(["--provider", "openrouter", "--model", self.default_model])

        # Quiet mode for programmatic use
        cmd.extend(["-Q", "--format", "stream-json"])

        if continue_last:
            cmd.extend(["-c"])
        elif session_id:
            cmd.extend(["-r", session_id])

        cmd.extend(["--in", str(self.workspace)])
        cmd.append(prompt)

        logger.debug("Running Hermes: %s", " ".join(cmd))

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.workspace),
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await self._process.communicate()
        output = stdout.decode("utf-8", errors="replace")
        error = stderr.decode("utf-8", errors="replace")

        if self._process.returncode != 0:
            logger.warning("Hermes exited with code %d: %s", self._process.returncode, error)

        session_id = self._extract_session_id(output)

        return output, session_id

    def _extract_session_id(self, output: str) -> str | None:
        """Extract Hermes session ID from JSONL output."""
        # Hermes stream-json outputs JSONL, look for session_id in metadata
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "session_id" in data:
                    return data["session_id"]
                if "meta" in data and "session_id" in data["meta"]:
                    return data["meta"]["session_id"]
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

        # Parse JSONL output for final result
        parsed_output = self._parse_jsonl_output(output)

        return AgentResult(
            success=success,
            output=parsed_output,
            error=output.strip() if not success else None,
            duration_seconds=duration,
            session_id=self._current_session_id,
        )

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

    def _parse_jsonl_output(self, output: str) -> str:
        """Parse Hermes stream-json output, extracting assistant messages."""
        messages = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                # Hermes stream-json format varies; look for content
                if "content" in data and data.get("role") == "assistant":
                    messages.append(data["content"])
                elif "text" in data:
                    messages.append(data["text"])
            except json.JSONDecodeError:
                continue
        return "\n".join(messages) if messages else output.strip()

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
        """Stream events during Hermes execution."""
        yield AgentEvent(type="start", message="Starting Hermes task...")
        result = await self.run_task(task)
        if result.success:
            yield AgentEvent(
                type="complete",
                message="Task completed",
                data={"result": result.output},
            )
        else:
            yield AgentEvent(type="error", message=result.error or "Task failed")
