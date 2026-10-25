"""Textual TUI for SAGE (hermes-style interface).

This is the interactive terminal UI used when stdout is a TTY. It drives the
same `ConversationLoop` as the ANSI CLI / Web UI through the thin callback
contract (`on_delta`, `on_event`, `turn_start`, `turn_end`), so the core loop
and application are untouched.

Layout:
  Header   — SAGE · model · workspace
  Feed     — scrollable conversation (user + SAGE + tool activity)
  Status   — active employee / pending approvals / time
  Input    — persistent prompt box with ↑/↓ history
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from app.conversation.models import AssistantResponse, LoopEvent


class SageTui(App):
    """Textual application shell for SAGE."""

    CSS = """
    #feed {
        height: 1fr;
        border: round $primary;
        padding: 1;
        overflow-y: scroll;
    }
    #status {
        height: 3;
        border: round $accent;
        padding: 0 1;
    }
    #input-row {
        height: 3;
        padding: 0 1;
    }
    Input {
        border: round $accent;
    }
    #thinking {
        height: 1;
        padding: 0 1;
        color: $warning;
        display: none;
    }
    #thinking.visible {
        display: block;
    }
    """

    BINDINGS: ClassVar[list[tuple]] = [
        ("ctrl+d", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        model_ids: list[str],
        workspace: str,
        on_submit,
        completion_notifier,
        status_provider=None,
    ) -> None:
        super().__init__()
        self._model_ids = model_ids
        self._workspace = workspace
        self._on_submit = on_submit  # async fn(message) -> AssistantResponse
        self._notifier = completion_notifier
        self._status_provider = status_provider or (lambda: None)
        self._history: list[str] = []
        self._history_idx: int | None = None
        self._busy = False

    # ---- compose ----

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            self.feed = RichLog(highlight=True, markup=True, wrap=True)
            yield self.feed
            yield Static("", id="thinking")
            yield Static("", id="status")
            with Horizontal(id="input-row"):
                yield Input(placeholder="Message SAGE…", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.feed.write(
            f"[bold green]SAGE[/] [dim]· {', '.join(self._model_ids) or 'none'}[/]"
        )
        self.feed.write(f"[dim]workspace: {self._workspace}[/]")
        self.feed.write("[dim]type /help in the box, or Ctrl+D to quit[/]")
        self.feed.write("")
        self._poll_notices()
        self._refresh_status()

    # ---- input ----

    @on(Input.Submitted, "#input")
    def _on_submit(self, event: Input.Submitted) -> None:
        message = event.value.strip()
        if not message:
            event.input.value = ""
            return
        self._history.append(message)
        self._history_idx = None
        event.input.value = ""
        self.feed.write(f"[bold cyan]You:[/] {message}")
        # Launch as a Textual worker (pass a coroutine; do NOT also @work-decorate).
        self.run_worker(self._handle_turn(message), group="turn", exclusive=False)

    def _on_key(self, event) -> None:
        # ↑/↓ history navigation
        if event.key == "up":
            if self._history:
                self._history_idx = (
                    len(self._history) - 1
                    if self._history_idx is None
                    else max(0, self._history_idx - 1)
                )
                inp = self.query_one("#input", Input)
                inp.value = self._history[self._history_idx]
                event.stop()
        elif event.key == "down" and self._history_idx is not None:
            self._history_idx += 1
            inp = self.query_one("#input", Input)
            if self._history_idx >= len(self._history):
                self._history_idx = None
                inp.value = ""
            else:
                inp.value = self._history[self._history_idx]
            event.stop()

    # ---- turn handling ----

    async def _handle_turn(self, message: str) -> None:
        if self._busy:
            self.feed.write("[yellow](SAGE is busy — try again shortly)[/]")
            return
        self._busy = True
        self._set_thinking(True)

        if message.startswith("/"):
            self._set_thinking(False)
            await self._run_command(message)
            self._busy = False
            self._set_thinking(False)
            return

        from app.conversation.models import UserMessage

        try:
            response = await self._on_submit(UserMessage(content=message))
            await self.turn_end(response)
            # Drain notices surfaced during the turn.
            self._drain_notices()
        except Exception as exc:
            self.feed.write(f"[red]✗ error:[/] {exc}")
        finally:
            self._busy = False
            self._set_thinking(False)
            self._refresh_status()
            self.query_one("#input", Input).focus()

    async def _run_command(self, message: str) -> None:
        # Commands are handled by main.py via the injected handler; for the
        # TUI we support a minimal built-in set and forward the rest.
        cmd, _, _arg = message.partition(" ")
        if cmd in ("/exit", "/quit"):
            self.exit()
            return
        self.feed.write(f"[dim]{message}[/] — (run via CLI handler)[/]")
        self._drain_notices()

    # ---- loop callback surface (used by main.py to wire the loop) ----
    # NOTE: do NOT name this `on_event` — Textual's App reserves `on_event`
    # for its own UI events (mount/unmount/message), so our loop handler must
    # use a distinct name to avoid the collision that crashed the app.

    def turn_start(self) -> None:
        self._set_thinking(True)

    async def sage_on_event(self, event: LoopEvent) -> None:
        if event.type == "llm_start":
            self._set_thinking(True)
        elif event.type == "tool_start":
            name = event.tool_name or "?"
            summary = self._summarize_args(event.arguments)
            suffix = f" [dim]({summary})[/]" if summary else ""
            self.feed.write(f"  [dim]⚙[/] [yellow]{name}[/]{suffix}")
        elif event.type == "tool_end":
            dur = f"{event.duration_ms:.0f}ms" if event.duration_ms is not None else ""
            if event.success:
                self.feed.write(f"      [green]✓[/] [dim]{dur}[/]")
            else:
                err = (event.error or "failed").split("\n")[0][:100]
                self.feed.write(f"      [red]✗[/] {err} [dim]{dur}[/]")
        elif event.type == "approval":
            target = event.resource or "target"
            self.feed.write(
                f"  [yellow]✋ approval required[/] · {event.tool_name} → "
                f"[bold]{target}[/]"
            )
        elif event.type == "compaction":
            self.feed.write("  [dim](compacting context…)[/]")

    async def on_delta(self, text: str) -> None:
        self._set_thinking(False)
        self.feed.write(text, end="")

    async def turn_end(self, response: AssistantResponse) -> None:
        self._set_thinking(False)
        self.feed.write("")
        elapsed = time.perf_counter() - getattr(self, "_turn_started", 0.0)
        stats = [f"{elapsed:.1f}s"]
        if response.input_tokens or response.output_tokens:
            i = f"{response.input_tokens / 1000:.1f}k" if response.input_tokens else "0"
            o = f"{response.output_tokens / 1000:.1f}k" if response.output_tokens else "0"
            stats.append(f"{i} in / {o} out")
        if response.model:
            stats.append(response.model)
        self.feed.write("  [dim]· [/]".join([f"[dim]{s}[/]" for s in stats]))
        self._busy = False
        self._refresh_status()

    # ---- helpers ----

    @staticmethod
    def _summarize_args(arguments: dict | None) -> str:
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

    def _set_thinking(self, on: bool) -> None:
        t = self.query_one("#thinking", Static)
        t.set_class(on, "visible")
        t.update("⠋ thinking…" if on else "")

    def _refresh_status(self) -> None:
        status = self.query_one("#status", Static)
        extra = self._status_provider() or ""
        parts = [datetime.now().strftime("%H:%M:%S")]
        if extra:
            parts.append(extra)
        status.update(f"[bold]● SAGE[/]  [dim]{' · '.join(parts)}[/]")

    @work(thread=False, group="poll")
    async def _poll_notices(self) -> None:
        while True:
            self._drain_notices()
            self._refresh_status()
            await asyncio.sleep(1.0)

    def _drain_notices(self) -> None:
        if not self._notifier:
            return
        for notice in self._notifier.drain():
            self.feed.write(f"[dim]· {notice}[/]")

    def action_quit(self) -> None:
        self.exit()
