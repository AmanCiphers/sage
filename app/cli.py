"""Terminal presentation for the SAGE CLI: a clean, modern TUI.

Zero dependencies — plain ANSI escapes only, disabled automatically when
stdout is not a TTY or NO_COLOR is set (https://no-color.org).

The renderer owns all terminal output. main.py drives it through a small
public surface (banner, prompt, messages, notices, spinner, approvals) and
feeds it loop events for live tool/employee activity.
"""

import asyncio
import contextlib
import os
import sys
import time

from app.conversation.models import AssistantResponse, LoopEvent


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def terminal_size() -> tuple[int, int]:
    try:
        return os.get_terminal_size()
    except OSError:
        return (80, 24)


# ─────────────────────────────────────────────────────────────────────────
# Styling
# ─────────────────────────────────────────────────────────────────────────


class Style:
    """ANSI styling, auto-disabled on non-TTY or NO_COLOR."""

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

    def blue(self, text: str) -> str:
        return self._wrap("34", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def magenta(self, text: str) -> str:
        return self._wrap("35", text)

    def inverse(self, text: str) -> str:
        return self._wrap("7", text)

    def underline(self, text: str) -> str:
        return self._wrap("4", text)


# ─────────────────────────────────────────────────────────────────────────
# Spinner
# ─────────────────────────────────────────────────────────────────────────


class Spinner:
    """Single-line spinner; stops cleanly before any other content."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

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
            f = self._FRAMES[frame % len(self._FRAMES)]
            print(f"\r\x1b[K{self.style.dim(f'{f} {label}')}", end="", flush=True)
            self._painted = True
            frame += 1
            await asyncio.sleep(0.08)

    async def stop(self) -> None:
        self._active = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._painted and self.style.enabled:
            print("\r\x1b[K", end="", flush=True)
        self._painted = False


# ─────────────────────────────────────────────────────────────────────────
# Renderer
# ─────────────────────────────────────────────────────────────────────────


def _fmt_args(arguments: dict | None) -> str:
    """Compact a tool-call argument dict for the activity line."""
    if not arguments:
        return ""
    parts = []
    for k, v in arguments.items():
        if k in ("content", "prompt", "objective", "command"):
            parts.append(f"{k}=<{len(str(v))} chars>")
        else:
            s = str(v)
            parts.append(f"{k}={s[:40]}{'…' if len(s) > 40 else ''}")
    return ", ".join(parts[:2])


class CliRenderer:
    """Draws the SAGE conversation, tool activity, notices, and prompts.

    Design goals (from the overhaul):
      - Clear role labeling and timestamped message prefixes.
      - Live tool-call activity lines with durations.
      - A status footer showing elapsed time, tool calls, tokens, model.
      - Structured approval prompts.
      - Clean help/memory/tools output via dedicated print helpers.
    """

    def __init__(self, style: Style | None = None) -> None:
        self.style = style or Style()
        self.spinner = Spinner(self.style)
        self._prefix_printed = False
        self._tool_calls = 0
        self._turn_started_at: float | None = None
        self._current_tool_start: float | None = None

    # ---- startup ----

    def banner(self, model_ids: list[str], workspace: str) -> None:
        title = self.style.bold(self.style.green("SAGE"))
        models = ", ".join(model_ids) or "none"
        print()
        print(f"  {title}  {self.style.dim('· ' + models)}")
        print(f"  {self.style.dim('workspace: ' + workspace)}")
        print(f"  {self.style.dim('type /help for commands')}")
        print()

    # ---- turn lifecycle ----

    def turn_start(self) -> None:
        self._prefix_printed = False
        self._tool_calls = 0
        self._turn_started_at = time.perf_counter()
        self._current_tool_start = None

    async def _stop_spinner(self) -> None:
        if self.spinner._active:
            await self.spinner.stop()

    async def on_event(self, event: LoopEvent) -> None:
        """Live activity feed from loop events."""
        if event.type == "llm_start":
            await self.spinner.start("thinking")
            return

        await self._stop_spinner()

        if event.type == "tool_start":
            self._tool_calls += 1
            self._current_tool_start = time.perf_counter()
            name = self.style.yellow(event.tool_name or "?")
            summary = _fmt_args(event.arguments)
            suffix = f"  {self.style.dim('(' + summary + ')')}" if summary else ""
            print(f"    {self.style.dim('⚙')} {name}{suffix}")

        elif event.type == "tool_end":
            dur = self._fmt_duration(event.duration_ms)
            if event.success:
                print(f"      {self.style.green('✓')} {self.style.dim(dur)}")
            else:
                err = (event.error or "failed").split("\n")[0][:100]
                print(f"      {self.style.red('✗')} {err} {self.style.dim(dur)}")

        elif event.type == "approval":
            target = event.resource or "target"
            print(
                f"    {self.style.yellow('✋')} {self.style.bold('approval required')} "
                f"{self.style.dim('·')} {event.tool_name} → {self.style.bold(str(target))}"
            )

        elif event.type == "compaction":
            print(f"    {self.style.dim('(compacting context…)')}")

    async def on_delta(self, text: str) -> None:
        await self._stop_spinner()
        if not self._prefix_printed:
            print(f"{self.style.bold(self.style.cyan('SAGE'))}: ", end="", flush=True)
            self._prefix_printed = True
        print(text, end="", flush=True)

    @staticmethod
    def _fmt_duration(duration_ms: float | None) -> str:
        if duration_ms is None:
            return ""
        if duration_ms < 1:
            return f"{duration_ms * 1000:.0f}µs"
        if duration_ms < 1000:
            return f"{duration_ms:.0f}ms"
        return f"{duration_ms / 1000:.1f}s"

    async def turn_end(self, response: AssistantResponse) -> None:
        await self._stop_spinner()
        if (response.content or self._prefix_printed):
            print()

        elapsed = (
            time.perf_counter() - self._turn_started_at
            if self._turn_started_at is not None
            else 0.0
        )
        stats = [f"{elapsed:.1f}s"]
        if self._tool_calls:
            stats.append(f"{self._tool_calls} tool call{'s' if self._tool_calls != 1 else ''}")
        if response.input_tokens or response.output_tokens:
            i = f"{response.input_tokens / 1000:.1f}k" if response.input_tokens else "0"
            o = f"{response.output_tokens / 1000:.1f}k" if response.output_tokens else "0"
            stats.append(f"{i} in / {o} out")
        if response.model:
            stats.append(response.model)
        print(f"  {self.style.dim('· '.join(stats))}")

    # ---- user prompt ----

    @property
    def prompt(self) -> str:
        return self.style.bold(self.style.cyan("You: "))

    # ---- notices / errors ----

    def notice(self, text: str) -> None:
        print(self.style.dim(text))

    def system_note(self, text: str) -> None:
        print(f"  {self.style.dim('·')} {self.style.dim(text)}")

    def error(self, message: str) -> None:
        print(f"\n{self.style.red('✗ error:')} {message}")

    # ---- approvals ----

    def approval_prompt(self, tool_name: str, resource: str | None) -> str:
        target = resource or "target"
        return (
            f"\n{self.style.yellow('▌')} {self.style.bold('Approval Required')}\n"
            f"  {self.style.dim('tool')}   {self.style.yellow(tool_name)}\n"
            f"  {self.style.dim('target')} {self.style.bold(str(target))}\n\n"
            f"  {self.style.green('Approve')} {self.style.dim('[y/N]')}  "
        )

    # ---- command output helpers ----

    def section(self, title: str) -> None:
        print()
        print(f"  {self.style.bold(self.style.cyan(title))}")
        print(f"  {self.style.dim('─' * 60)}")

    def help_menu(self, rows: list[tuple[str, str]]) -> None:
        self.section("Commands")
        for cmd, desc in rows:
            print(f"  {self.style.yellow(cmd.ljust(20))} {self.style.dim(desc)}")
        print()

    def tool_list(self, tools: list[tuple[str, str, str]]) -> None:
        """tools: [(name, classification, flags)]."""
        self.section("Tools")
        for name, cls, flags in tools:
            flag_suffix = self.style.dim(f" [{flags}]") if flags else ""
            print(
                f"  {self.style.green(name.ljust(28))} "
                f"{self.style.dim(cls)}{flag_suffix}"
            )
        print()
