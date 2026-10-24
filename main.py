import asyncio
import re
import threading

from app.application import SAGEApplication
from app.cli import CliRenderer
from app.config import settings
from app.conversation.models import AssistantResponse, UserMessage
from app.observability.logging import configure_logging

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


async def main() -> None:
    configure_logging()

    app = SAGEApplication()
    await app.start()

    renderer = CliRenderer()
    loop = app.conversation_engine
    loop.on_event = renderer.on_event

    models = [
        metadata.model_id
        for metadata in app.model_registry.list()
        if metadata.available
    ]
    renderer.banner(
        models,
        str(settings.workspace_path.expanduser().resolve()),
    )

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
            print(
                "\n".join(
                    [
                        renderer.style.dim("commands:"),
                        "  /help            show this help",
                        "  /tools           list available tools",
                        "  /memory          show raw long-term memory",
                        "  /profile         show structured profile",
                        "  /search <query>  search conversation history",
                        "  /yolo            allow all actions without approval (full permission)",
                        "  /approvals       require approval again (exit full-permission mode)",
                        "  /exit            quit",
                    ]
                )
            )

        elif command == "/tools":
            for tool in app.tool_registry.list():
                flags = []
                if tool.destructive:
                    flags.append("destructive")
                if tool.requires_approval:
                    flags.append("may need approval")
                suffix = renderer.style.dim(f" [{', '.join(flags)}]") if flags else ""
                name = renderer.style.yellow(tool.name)
                pad = " " * max(0, 28 - len(tool.name))
                print(
                    f"  {name}{pad} "
                    f"{renderer.style.dim(tool.classification.value)}{suffix}"
                )

        elif command in {"/memory", "/profile"}:
            content = app.memory_store.load().strip()
            if not content:
                print(renderer.style.dim("(memory is empty)"))
                return None

            if command == "/memory":
                print(content)
                return None

            # /profile — structured view
            print(renderer.style.bold("Profile: Aman"))
            print(renderer.style.dim("─" * 40))
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                # memory lines look like "- Fact" or "• Fact"
                fact = line.lstrip("-• ").strip()
                if ": " in fact:
                    k, v = fact.split(": ", 1)
                    print(f"  {renderer.style.cyan(k)}: {v}")
                else:
                    print(f"  {fact}")
            return None

        elif command == "/search":
            if not argument.strip():
                print(renderer.style.dim("usage: /search <query>"))
            else:
                rows = await app.message_store.search(argument, limit=5)
                if not rows:
                    print(renderer.style.dim("no matches"))
                for row in rows:
                    content = " ".join(str(row.get("content", "")).split())
                    if len(content) > 160:
                        content = content[:157] + "..."
                    print(
                        f"  {renderer.style.cyan(str(row.get('role', '?')))} "
                        f"{content}"
                    )

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

        elif command == "/approvals":
            # List pending approvals
            pending = app.approval_manager.list_pending()
            if not pending:
                print(renderer.style.dim("No pending approvals"))
            else:
                for p in pending:
                    print(f"  {p['approval_id']}: {p.get('description', 'unknown')}")

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
            for line in iter(input, ""):
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
                # No input this cycle — surface any pending notices, then keep
                # waiting for input.
                for notice in app.completion_notifier.drain():
                    renderer.note(f"\n[SAGE] {notice}")
                continue

            # Safe boundary: SAGE is idle here (no response streaming), so
            # any employee-completion notices can be shown without cutting
            # into a reply.
            for notice in app.completion_notifier.drain():
                renderer.note(f"\n[SAGE] {notice}")

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
