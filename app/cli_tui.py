"""Textual TUI for SAGE (hermes-style interface).

This is the interactive terminal UI used when stdout is a TTY. It drives the
same `ConversationLoop` as the ANSI CLI / Web UI through the thin callback
contract (`on_delta`, `on_event`, `turn_start`, `turn_end`), so the core loop
and application are untouched.

Layout:
  Header   — SAGE · model · workspace
  Main     — chat feed (message bubbles) + right-side status panel
  Dock     — live response, thinking indicator, status line, input box

Approvals use a modal dialog; commands and message rendering are handled by
dedicated widgets so the chat reads cleanly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Markdown, Static

from app.conversation.models import AssistantResponse, LoopEvent, UserMessage

_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class ApprovalModal(ModalScreen[bool]):
    """Modal dialog asking the user to approve or deny a tool action."""

    CSS = """
    ApprovalModal {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    #dialog-title {
        color: $warning;
        text-style: bold;
    }
    #dialog-body {
        margin-top: 1;
        margin-bottom: 1;
    }
    #dialog-hint {
        color: $text-muted;
    }
    """

    def __init__(self, tool_name: str, resource: str | None, description: str = "") -> None:
        super().__init__()
        self._tool = tool_name
        self._resource = resource or "target"
        self._desc = description

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("⚠ Approval Required", id="dialog-title")
            yield Static(
                f"Tool: [bold]{self._tool}[/]\n"
                f"Target: [bold]{self._resource}[/]"
                + (f"\n{self._desc}" if self._desc else ""),
                id="dialog-body",
            )
            yield Static(
                "[b]y[/] approve · [b]n[/]/[b]esc[/] deny",
                id="dialog-hint",
            )

    def on_key(self, event) -> None:
        if event.key in ("y", "enter"):
            self.dismiss(True)
            event.stop()
        elif event.key in ("n", "escape"):
            self.dismiss(False)
            event.stop()


class SageTui(App):
    """Textual application shell for SAGE."""

    CSS = """
    #layout {
        width: 100%;
        height: 100%;
    }
    #main {
        width: 100%;
        height: 1fr;
    }
    #feed {
        width: 1fr;
        height: 100%;
        border: round $primary 50%;
        padding: 0 1;
        overflow-y: scroll;
        background: $surface;
    }
    #feed .msg {
        width: 100%;
        height: auto;
        margin: 0 1 1 1;
    }
    #feed Markdown {
        width: 100%;
        height: auto;
        padding: 0 2 0 0;
    }
    #feed .msg-user {
        color: $text;
    }
    #feed .msg-sage {
        color: $text;
    }
    #feed .msg-label {
        color: $accent;
        text-style: bold;
        margin-bottom: 0;
    }
    #feed .msg-rule {
        color: $primary 40%;
    }
    #feed .tool-line {
        color: $text-muted;
        margin-left: 1;
        height: auto;
    }
    #side {
        width: 32;
        height: 100%;
        border: round $accent 50%;
        padding: 1;
        margin-left: 1;
        background: $panel;
        display: none;
    }
    #side.visible {
        display: block;
    }
    #side-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    #side-body {
        color: $text;
    }
    #dock {
        width: 100%;
        height: auto;
        dock: bottom;
        padding: 0 1 1 1;
    }
    #status {
        width: 100%;
        height: 1;
        border: none;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }
    #input-row {
        width: 100%;
        height: 3;
    }
    Input {
        width: 100%;
        border: round $accent;
    }
    #thinking {
        width: 100%;
        height: 1;
        padding: 0 2;
        color: $warning;
        display: none;
    }
    #thinking.visible {
        display: block;
    }
    #live-response {
        width: 100%;
        padding: 0 2;
        color: $text;
        background: $surface;
    }
    """

    BINDINGS: ClassVar[list[tuple]] = [
        ("ctrl+d", "quit", "Quit"),
        ("tab", "toggle_side", "Status"),
    ]

    def __init__(
        self,
        *,
        model_ids: list[str],
        workspace: str,
        on_submit,
        completion_notifier,
        approval_callback=None,
        status_provider=None,
    ) -> None:
        super().__init__()
        self._model_ids = model_ids
        self._workspace = workspace
        self._on_submit = on_submit  # async fn(message) -> AssistantResponse
        self._approval_callback = approval_callback
        self._notifier = completion_notifier
        self._status_provider = status_provider or (lambda: None)
        self._history: list[str] = []
        self._history_idx: int | None = None
        self._busy = False
        self._live_text = ""
        self._live = None
        self._live_md: Vertical | None = None
        self._think_task: asyncio.Task | None = None
        self._think_idx = 0

    # ---- compose ----

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="layout"):
            with Horizontal(id="main"):
                self.feed = Vertical(id="feed")
                yield self.feed
                self.side = Static("", id="side")
                yield self.side
            with Vertical(id="dock"):
                yield Static("", id="thinking")
                yield Static("", id="status")
                with Horizontal(id="input-row"):
                    yield Input(placeholder="Message SAGE…", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self._welcome()
        self._poll_notices()
        self._refresh_status()

    def _welcome(self) -> None:
        self._write_rule()
        self._feed_write(f"[bold green]SAGE[/] [dim]· {', '.join(self._model_ids) or 'none'}[/]")
        self._feed_write(f"[dim]workspace: {self._workspace}[/]")
        self._feed_write("[dim]type /help in the box · Tab for status · Ctrl+D to quit[/]")
        self._write_rule()

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
        self._add_user_message(message)
        self.run_worker(self._handle_turn(message), group="turn", exclusive=False)

    def on_key(self, event) -> None:
        # Only take over ↑/↓ when the input box is focused (history nav).
        if self.focused is None or self.focused.id != "input":
            return
        if event.key == "up":
            if self._history:
                self._history_idx = (
                    len(self._history) - 1
                    if self._history_idx is None
                    else max(0, self._history_idx - 1)
                )
                self.query_one("#input", Input).value = self._history[self._history_idx]
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
            self._feed_write("[yellow](SAGE is busy — try again shortly)[/]")
            return
        self._busy = True
        self._live_text = ""
        self._set_thinking(True)

        if message.startswith("/"):
            self._set_thinking(False)
            await self._run_command(message)
            self._busy = False
            self._set_thinking(False)
            self._refresh_status()
            self.query_one("#input", Input).focus()
            return

        try:
            response = await self._on_submit(UserMessage(content=message))
            # Handle any pending approvals the loop surfaced.
            while response and response.pending_approval:
                approved = await self._ask_approval(response.pending_approval)
                response = await self._resolve_approval(response.pending_approval, approved)
            await self.turn_end(response)
            self._drain_notices()
        except Exception as exc:
            self._feed_write(f"[red]✗ error:[/] {exc}")
        finally:
            self._busy = False
            self._set_thinking(False)
            self._refresh_status()
            self.query_one("#input", Input).focus()

    async def _ask_approval(self, approval: dict) -> bool:
        tool = approval.get("tool_name", "?")
        resource = approval.get("resource") or approval.get("arguments", {}).get("path")
        modal = ApprovalModal(tool, resource)
        decision = await self.push_screen_wait(modal)
        if decision:
            self._feed_write(f"  [green]✓ approved[/] {tool}")
        else:
            self._feed_write(f"  [dim]✗ denied[/] {tool}")
        return bool(decision)

    async def _resolve_approval(self, approval: dict, approved: bool) -> AssistantResponse:
        if self._approval_callback is None:
            return AssistantResponse(content="")
        return await self._approval_callback(approval, approved)

    async def _run_command(self, message: str) -> None:
        cmd, _, _arg = message.partition(" ")
        if cmd in ("/exit", "/quit"):
            self.exit()
            return
        self._feed_write(f"[dim]{message}[/] — (run via CLI handler)[/]")
        self._drain_notices()

    # ---- loop callback surface (used by main.py to wire the loop) ----
    # NOTE: do NOT name this `on_event` — Textual's App reserves `on_event`.

    def turn_start(self) -> None:
        self._set_thinking(True)

    async def sage_on_event(self, event: LoopEvent) -> None:
        if event.type == "llm_start":
            self._set_thinking(True)
        elif event.type == "tool_start":
            name = event.tool_name or "?"
            summary = self._summarize_args(event.arguments)
            suffix = f" [dim]({summary})[/]" if summary else ""
            self._feed_write(f"  [dim]⚙[/] [yellow]{name}[/]{suffix}")
        elif event.type == "tool_end":
            dur = f"{event.duration_ms:.0f}ms" if event.duration_ms is not None else ""
            if event.success:
                self._feed_write(f"      [green]✓[/] [dim]{dur}[/]")
            else:
                err = (event.error or "failed").split("\n")[0][:100]
                self._feed_write(f"      [red]✗[/] {err} [dim]{dur}[/]")
        elif event.type == "approval":
            target = event.resource or "target"
            self._feed_write(
                f"  [yellow]✋ approval required[/] · {event.tool_name} → "
                f"[bold]{target}[/]"
            )
        elif event.type == "compaction":
            self._feed_write("  [dim](compacting context…)[/]")

    async def on_delta(self, text: str) -> None:
        self._set_thinking(False)
        self._live_text += text
        if self._live_md is None:
            self._live = Markdown("")
            self._live_md = Vertical(
                Static("[bold cyan]SAGE[/]", classes="msg-label"),
                self._live,
                classes="msg msg-sage",
            )
            self.feed.mount(self._live_md)
        self._live.update(self._live_text)
        self._scroll_bottom()

    async def turn_end(self, response: AssistantResponse) -> None:
        self._set_thinking(False)
        if self._live_text:
            self._write_rule()
            self._live_text = ""
        self._live = None
        self._live_md = None
        elapsed = time.perf_counter() - getattr(self, "_turn_started", 0.0)
        stats = [f"{elapsed:.1f}s"]
        if response.input_tokens or response.output_tokens:
            i = f"{response.input_tokens / 1000:.1f}k" if response.input_tokens else "0"
            o = f"{response.output_tokens / 1000:.1f}k" if response.output_tokens else "0"
            stats.append(f"{i} in / {o} out")
        if response.model:
            stats.append(response.model)
        self._feed_write("  [dim]· [/]".join(f"[dim]{s}[/]" for s in stats))
        self._busy = False
        self._refresh_status()

    # ---- chat rendering ----

    def _feed_write(self, rich_markup: str) -> None:
        """Append a plain rich-text line to the feed container."""
        self.feed.mount(Static(rich_markup, classes="msg tool-line"))
        self._scroll_bottom()

    def _add_user_message(self, text: str) -> None:
        self._write_rule()
        self.feed.mount(
            Vertical(
                Static("[bold cyan]YOU[/]", classes="msg-label"),
                Static(_strip_ansi(text)),
                classes="msg msg-user",
            )
        )
        self._write_rule()
        self._scroll_bottom()

    def _add_sage_message(self, text: str) -> None:
        self.feed.mount(
            Vertical(
                Static("[bold cyan]SAGE[/]", classes="msg-label"),
                Markdown(text),
                classes="msg msg-sage",
            )
        )
        self._write_rule()
        self._scroll_bottom()

    def _write_rule(self) -> None:
        width = max(20, self.size.width - 8)
        self.feed.mount(Static(f"[dim]{'─' * width}[/]", classes="msg-rule"))
        self._scroll_bottom()

    def _scroll_bottom(self) -> None:
        self.feed.scroll_end(animate=False)

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
        if on:
            self._start_thinking_animation()
        else:
            self._stop_thinking_animation()

    def _start_thinking_animation(self) -> None:
        if self._think_task is not None:
            return
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._think_idx = 0

        async def _animate() -> None:
            t = self.query_one("#thinking", Static)
            while True:
                f = frames[self._think_idx % len(frames)]
                t.update(f"[warning]{f}[/] thinking…")
                self._think_idx += 1
                await asyncio.sleep(0.1)

        self._think_task = asyncio.create_task(_animate())

    def _stop_thinking_animation(self) -> None:
        if self._think_task is not None:
            self._think_task.cancel()
            self._think_task = None
        self.query_one("#thinking", Static).update("")

    def _refresh_status(self) -> None:
        status = self.query_one("#status", Static)
        extra = self._status_provider() or ""
        parts = [datetime.now().strftime("%H:%M:%S")]
        if extra:
            parts.append(extra)
        status.update(f"[bold]● SAGE[/]  [dim]{' · '.join(parts)}[/]")
        self._refresh_side()

    def _refresh_side(self) -> None:
        try:
            extra = self._status_provider() or ""
            self.side.update(f"[bold]# status[/]\n\n{extra}")
        except Exception:
            pass

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
            self._feed_write(f"[dim]· {notice}[/]")

    def action_quit(self) -> None:
        self.exit()

    def action_toggle_side(self) -> None:
        self.side.set_class(not self.side.has_class("visible"), "visible")
