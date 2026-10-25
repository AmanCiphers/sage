import asyncio
import os
import re
import sys
import threading

from app.application import SAGEApplication
from app.cli import CliRenderer
from app.config import settings
from app.conversation.models import AssistantResponse, UserMessage
from app.observability.logging import configure_logging

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def _roster_summary(app: SAGEApplication) -> str:
    """Short status string of active employees for the TUI status bar."""
    try:
        rows = app.supervisor.roster_status()
    except Exception:
        return ""
    active = [r["name"] for r in rows if r.get("status") == "active"]
    if active:
        return "⚡ " + ", ".join(active)
    return "standby"


def _use_tui() -> bool:
    """Use the Textual TUI on an interactive TTY (skip when NO_COLOR / non-TTY)."""
    if os.environ.get("SAGE_TUI") == "0":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


async def _run_tui(app: SAGEApplication, models: list[str], workspace: str) -> None:
    """Run the Textual TUI, driving the same conversation loop."""
    from app.cli_tui import SageTui

    loop = app.conversation_engine

    async def on_submit(message: UserMessage) -> AssistantResponse:
        return await loop.handle_stream(message, tui.on_delta)

    tui = SageTui(
        model_ids=models,
        workspace=workspace,
        on_submit=on_submit,
        completion_notifier=app.completion_notifier,
        status_provider=lambda: _roster_summary(app),
    )
    loop.on_event = tui.on_event
    await tui.run_async()
    await app.stop()


async def main() -> None:
    configure_logging()

    app = SAGEApplication()
    await app.start()

    models = [
        metadata.model_id
        for metadata in app.model_registry.list()
        if metadata.available
    ]
    workspace = str(settings.workspace_path.expanduser().resolve())

    if _use_tui():
        await _run_tui(app, models, workspace)
        return

    renderer = CliRenderer()
    loop = app.conversation_engine
    loop.on_event = renderer.on_event

    renderer.banner(models, workspace)

    async def run_turn(get_response) -> AssistantResponse:
        renderer.turn_start()
        try:
            response = await get_response()
        except Exception as exc:
            await renderer.spinner.stop()
            renderer.error(str(exc))
            return AssistantResponse(content="")
        await renderer.turn_end(response)
        return response

    async def respond(message: UserMessage) -> AssistantResponse:
        return await run_turn(
            lambda: loop.handle_stream(message, renderer.on_delta)
        )

    async def handle_command(text: str) -> str | None:
        """Returns 'exit' to quit, None if not a command."""

        command, _, argument = text.partition(" ")
        command = command.lower()

        if command in {"/exit", "/quit"}:
            return "exit"

        if command == "/help":
            renderer.help_menu(
                [
                    ("/help", "show this help"),
                    ("/tools", "list available tools"),
                    ("/memory", "show raw long-term memory"),
                    ("/profile", "show structured profile"),
                    ("/search <q>", "search conversation history"),
                    ("/jobs", "list scheduled jobs"),
                    ("/rules", "list proactive rules"),
                    ("/yolo", "full permission (no approvals)"),
                    ("/approvals", "re-enable approval prompts"),
                    ("/exit", "quit"),
                ]
            )

        elif command == "/tools":
            rows = []
            for tool in app.tool_registry.list():
                flags = []
                if tool.destructive:
                    flags.append("destructive")
                if tool.requires_approval:
                    flags.append("may need approval")
                rows.append(
                    (tool.name, tool.classification.value, ", ".join(flags))
                )
            renderer.tool_list(rows)

        elif command in {"/memory", "/profile"}:
            content = app.memory_store.load().strip()
            if not content:
                print(renderer.style.dim("(memory is empty)"))
                return None

            if command == "/memory":
                renderer.section("Memory")
                for line in content.splitlines():
                    print(f"  {line}")
                print()
                return None

            # /profile — structured view
            renderer.section("Profile")
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                fact = line.lstrip("-• ").strip()
                if ": " in fact:
                    k, v = fact.split(": ", 1)
                    print(f"  {renderer.style.cyan(k)}: {v}")
                else:
                    print(f"  {fact}")
            print()
            return None

        elif command == "/search":
            if not argument.strip():
                renderer.system_note("usage: /search <query>")
            else:
                rows = await app.message_store.search(argument, limit=5)
                if not rows:
                    renderer.system_note("no matches")
                for row in rows:
                    content = " ".join(str(row.get("content", "")).split())
                    if len(content) > 160:
                        content = content[:157] + "..."
                    print(
                        f"  {renderer.style.cyan(str(row.get('role', '?')).ljust(10))} "
                        f"{content}"
                    )

        elif command == "/jobs":
            jobs = await app.scheduler.list_jobs()
            renderer.section("Scheduled Jobs")
            if not jobs:
                renderer.system_note("no scheduled jobs")
            for j in jobs:
                print(
                    f"  {renderer.style.bold(j.name)}  {renderer.style.dim(j.job_id)}  "
                    f"{'✓' if j.enabled else '✗'}  next: {j.next_run_at or '—'}"
                )
            print()

        elif command == "/rules":
            rules = await app.proactive.list_rules()
            renderer.section("Proactive Rules")
            if not rules:
                renderer.system_note("no proactive rules")
            for r in rules:
                print(
                    f"  {renderer.style.bold(r.name)}  {renderer.style.dim(r.rule_id)}  "
                    f"{'✓' if r.enabled else '✗'}  {r.trigger_event_type} → {r.action_event_type}"
                )
            print()

        elif command in {"/yolo", "/approvals"}:
            # /yolo = full permission (no approvals); /approvals = back to interactive
            from app.permissions.config import set_yolo_mode

            if command == "/yolo":
                set_yolo_mode(True)
                print(renderer.style.green(
                    "Full permission ON — all actions will run without approval. "
                    "Use /approvals to re-enable approval prompts."
                ))
            else:
                set_yolo_mode(False)
                print(renderer.style.dim("Approval prompts re-enabled."))
            return None

        elif command == "/allowlist":
            parts = argument.strip().split()
            if not parts or parts[0] == "list":
                session = app.permission_engine.allowlist.list_session()
                permanent = app.permission_engine.allowlist.list_permanent()
                print(renderer.style.dim("Session allowlist:"))
                for p in session:
                    print(f"  {p}")
                print(renderer.style.dim("Permanent allowlist:"))
                for p in permanent:
                    print(f"  {p}")
            elif parts[0] == "add" and len(parts) > 1:
                pattern = " ".join(parts[1:])
                app.permission_engine.allow_permanent(pattern)
                print(renderer.style.green(f"Added to permanent allowlist: {pattern}"))
            elif parts[0] == "remove" and len(parts) > 1:
                pattern = " ".join(parts[1:])
                if app.permission_engine.revoke(pattern):
                    print(renderer.style.green(f"Removed from allowlist: {pattern}"))
                else:
                    print(renderer.style.red(f"Pattern not found: {pattern}"))

        elif command == "/approve":
            # Approve pending gateway approvals
            parts = argument.strip().split()
            if not parts or parts[0] == "all":
                # Approve all pending
                count = await app.approval_manager.approve_all()
                print(renderer.style.green(f"Approved {count} pending approval(s)"))
            else:
                approval_id = parts[0]
                app.approval_manager.approve(approval_id)
                print(renderer.style.green(f"Approved: {approval_id}"))

        elif command == "/deny":
            parts = argument.strip().split()
            if not parts:
                print(renderer.style.dim("usage: /deny <approval_id> [reason]"))
            elif parts[0] == "all":
                reason = " ".join(parts[1:]) if len(parts) > 1 else "Batch denied"
                count = await app.approval_manager.deny_all(reason)
                print(renderer.style.green(f"Denied {count} pending approval(s)"))
            else:
                approval_id = parts[0]
                reason = " ".join(parts[1:]) if len(parts) > 1 else ""
                app.approval_manager.deny(approval_id, reason)
                print(renderer.style.green(f"Denied: {approval_id}"))

        elif command == "/smart":
            # Smart guardian status
            sg = app.smart_guardian
            if sg:
                print(f"Smart guardian: {'enabled' if sg.enabled else 'disabled'}")
                print(f"Denial count: {sg.denial_count}")
                print(f"Circuit breaker threshold: {app.permission_engine.config.denial_breaker_threshold}")  # noqa: E501
            else:
                print(renderer.style.dim("Smart guardian not initialized"))

        else:
            print(renderer.style.dim(f"unknown command: {command} (try /help)"))

        return None

    # Single persistent stdin reader thread. It pushes each typed line onto
    # an asyncio queue; we consume from the queue with a timeout so background
    # notices (completions, reminders) surface while idle. Using one long-lived
    # thread (instead of to_thread + wait_for per line) avoids leaking threads
    # that would steal the user's input.
    input_queue: asyncio.Queue[str] = asyncio.Queue()

    def _read_stdin() -> None:
        try:
            while True:
                sys.stdout.write(renderer.prompt)
                sys.stdout.flush()
                line = input()
                input_queue.put_nowait(line)
        except (EOFError, KeyboardInterrupt):
            pass

    reader_thread = threading.Thread(target=_read_stdin, daemon=True)
    reader_thread.start()

    try:
        while True:
            # Wait briefly for a typed line so we can also surface background
            # notices (employee completions, reminders) while the user is idle.
            try:
                user_input = await asyncio.wait_for(input_queue.get(), timeout=1.0)
            except TimeoutError:
                for notice in app.completion_notifier.drain():
                    renderer.notice(f"[SAGE] {notice}")
                continue

            # Safe boundary: SAGE is idle here (no response streaming), so
            # any employee-completion notices can be shown without cutting
            # into a reply.
            for notice in app.completion_notifier.drain():
                renderer.notice(f"[SAGE] {notice}")

            user_input = _ANSI_RE.sub("", user_input).strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                outcome = await handle_command(user_input)
                if outcome == "exit":
                    break
                continue

            response = await respond(UserMessage(content=user_input))

            while response.pending_approval:
                approval = response.pending_approval
                try:
                    choice = await asyncio.to_thread(
                        input,
                        renderer.approval_prompt(
                            approval["tool_name"],
                            approval.get("resource"),
                        ),
                    )
                except (EOFError, KeyboardInterrupt):
                    print()
                    choice = "n"

                approved = choice.strip().lower() in {"y", "yes"}

                if approved:
                    response = await run_turn(
                        lambda a=approval: app.approve_pending(
                            a["approval_id"],
                            on_delta=renderer.on_delta,
                        )
                    )
                else:
                    response = await run_turn(
                        lambda a=approval: app.deny_pending(
                            a["approval_id"],
                            on_delta=renderer.on_delta,
                        )
                    )

    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
