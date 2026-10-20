"""Terminal presentation for the SAGE CLI: colors, spinner, event lines.

Zero dependencies — plain ANSI escapes, disabled automatically when
stdout is not a TTY or NO_COLOR is set (https://no-color.org).
"""

import asyncio
import os
import sys
import time

from app.conversation.models import AssistantResponse, LoopEvent


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


class Style:
    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = _color_enabled() if enabled is None else enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    """Single-line spinner on stdout; stops cleanly before any content."""

    def __init__(self, style: Style) -> None:
        self.style = style
        self._task: asyncio.Task | None = None
        self._active = False
        self._painted = False

    async def start(self, label: str = "thinking") -> None:
        if not self.style.enabled:
            return
        await self.stop()
        self._active = True
        self._painted = False
        self._task = asyncio.create_task(self._run(label))

    async def _run(self, label: str) -> None:
        frame = 0
        while self._active:
            line = _SPINNER_FRAMES[frame % len(_SPINNER_FRAMES)]
            painted = self.style.dim(f"{line} {label}")
            print(f"\r\x1b[K{painted}", end="", flush=True)
            self._painted = True
            frame += 1
            await asyncio.sleep(0.09)

    async def stop(self) -> None:
        self._active = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Clear only if WE painted the current line — never erase
        # streamed content that was printed after the spinner stopped.
        if self._painted and self.style.enabled:
            print("\r\x1b[K", end="", flush=True)
        self._painted = False


def summarize_arguments(tool_name: str, arguments: dict | None) -> str:
    if not arguments:
        return ""

    parts = []
    for key, value in arguments.items():
        if key == "content" or key == "prompt":
            parts.append(f"{key}=<{len(str(value))} chars>")
        else:
            text = str(value)
            if len(text) > 48:
                text = text[:45] + "..."
            parts.append(f"{key}={text}")
        if len(parts) == 2:
            break

    return ", ".join(parts)


class CliRenderer:
    """Turns loop events + deltas into terminal output."""

    def __init__(self, style: Style | None = None) -> None:
        self.style = style or Style()
        self.spinner = Spinner(self.style)
        self._prefix_printed = False
        self.tool_calls = 0
        self.started_at: float | None = None

    def banner(self, model_ids: list[str], workspace: str) -> None:
        title = self.style.bold(self.style.green("SAGE"))
        models = ", ".join(model_ids) or "none"
        print(title + " " + self.style.dim(f"· {models}"))
        print(self.style.dim(f"workspace: {workspace}"))
        print(self.style.dim("type /help for commands\n"))

    def turn_start(self) -> None:
        self._prefix_printed = False
        self.tool_calls = 0
        self.started_at = time.perf_counter()

    async def on_event(self, event: LoopEvent) -> None:
        if event.type == "llm_start":
            await self.spinner.start()
            return

        await self.spinner.stop()

        if event.type == "tool_start":
            self.tool_calls += 1
            summary = summarize_arguments(event.tool_name or "", event.arguments)
            name = self.style.yellow(event.tool_name or "?")
            line = f"  {self.style.dim('⚙')} {name}"
            if summary:
                line += f" {self.style.dim(f'({summary})')}"
            print(line)

        elif event.type == "tool_end":
            duration = (
                self.style.dim(f"{event.duration_ms:.0f}ms")
                if event.duration_ms is not None
                else ""
            )
            if event.success:
                print(f"    {self.style.green('✓')} {duration}")
            else:
                error = (event.error or "failed").split("\n")[0]
                if len(error) > 100:
                    error = error[:97] + "..."
                print(f"    {self.style.red('✗')} {error} {duration}")

        elif event.type == "approval":
            target = event.resource or "target"
            print(
                f"    {self.style.yellow('✋ approval required')} "
                f"· {event.tool_name} → {self.style.bold(str(target))}"
            )

        elif event.type == "compaction":
            print(self.style.dim("  (compacting context…)"))

    async def on_delta(self, text: str) -> None:
        await self.spinner.stop()
        if not self._prefix_printed:
            print("SAGE: ", end="", flush=True)
            self._prefix_printed = True
        print(text, end="", flush=True)

    async def turn_end(self, response: AssistantResponse) -> None:
        await self.spinner.stop()

        # Fallback path may not have streamed any delta.
        if not self._prefix_printed and response.content:
            print("SAGE: ", end="")
        if response.content or self._prefix_printed:
            print()

        elapsed = (
            time.perf_counter() - self.started_at
            if self.started_at is not None
            else 0.0
        )
        stats = f"{elapsed:.1f}s"
        if self.tool_calls:
            stats += f" · {self.tool_calls} tool call{'s' if self.tool_calls != 1 else ''}"
        # Token usage from provider (accumulated across iterations).
        if response.input_tokens is not None or response.output_tokens is not None:
            in_tok = f"{response.input_tokens / 1000:.1f}k" if response.input_tokens else "0"
            out_tok = f"{response.output_tokens / 1000:.1f}k" if response.output_tokens else "0"
            stats += f" · {in_tok} in / {out_tok} out"
        if response.model:
            stats += f" · {response.model}"
        print(self.style.dim(f"  ({stats})"))

    def error(self, message: str) -> None:
        print(f"\n{self.style.red('error:')} {message}")

    def approval_prompt(self, tool_name: str, resource: str | None) -> str:
        target = resource or "target"
        return (
            f"{self.style.yellow('Approve')} {self.style.bold(tool_name)} "
            f"{self.style.dim('on')} {self.style.bold(str(target))}? "
            f"{self.style.dim('[y/N]')} "
        )

    def note(self, text: str) -> None:
        print(self.style.dim(text))
