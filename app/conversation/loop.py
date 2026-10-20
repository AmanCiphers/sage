"""The agentic conversation loop.

Structure adapted from Hermes' conversation_loop:

    persist user turn
    build history (system + summary + recent tail)
    loop up to max_iterations:
        compact if the context window is filling up
        call the LLM with tools
        if the LLM asks for tools: permission-check, execute, feed results
          back, continue the loop
        otherwise: final answer, done

SAGE deviations from Hermes (see docs/DECISIONS.md):
- every tool call passes SAGE's deterministic PermissionEngine before
  execution; the LLM can never bypass it
- the loop never blocks on substantial work: start_task dispatches it to
  the background and the conversation stays free
"""

import inspect
import json
import time
from collections.abc import Awaitable, Callable

from app.context.builder import ContextBuilder
from app.context.compressor import ContextCompressor
from app.context.tokens import estimate_history_tokens
from app.conversation.models import AssistantResponse, LoopEvent, UserMessage
from app.execution.engine import ExecutionEngine
from app.execution.models import ToolCall
from app.inference.manager import InferenceManager
from app.inference.models import InferenceRequest, ModelRequirements
from app.observability.logging import get_logger, new_request_id
from app.permissions.approvals import ApprovalManager
from app.permissions.engine import PermissionEngine
from app.permissions.models import PermissionDecision
from app.persistence.store import MessageStore
from app.tools.registry import ToolRegistry


def arguments_resource(call: dict) -> str | None:
    """Best-effort human-facing target of a tool call (path, query, ...)."""

    raw = call.get("function", {}).get("arguments", "{}")
    try:
        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        return None

    return args.get("path") or args.get("query") or None


class ConversationLoop:

    def __init__(
        self,
        inference_manager: InferenceManager,
        tool_registry: ToolRegistry,
        permission_engine: PermissionEngine,
        execution_engine: ExecutionEngine,
        approval_manager: ApprovalManager,
        message_store: MessageStore,
        context_builder: ContextBuilder,
        compressor: ContextCompressor,
        max_iterations: int = 8,
        max_tool_result_chars: int = 8000,
    ) -> None:
        self.inference_manager = inference_manager
        self.tool_registry = tool_registry
        self.permission_engine = permission_engine
        self.execution_engine = execution_engine
        self.approval_manager = approval_manager
        self.message_store = message_store
        self.context_builder = context_builder
        self.compressor = compressor
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        self.logger = get_logger(__name__)

        # UI hook: notified of turn activity (LLM calls, tool rounds).
        # Set by the CLI/Web layer after construction.
        self.on_event: Callable[[LoopEvent], Awaitable[None]] | None = None

    async def _emit(self, event: LoopEvent) -> None:
        if self.on_event is None:
            return
        result = self.on_event(event)
        if inspect.isawaitable(result):
            await result

    async def handle(self, message: UserMessage) -> AssistantResponse:
        return await self._run_turn(message, on_delta=None)

    async def handle_stream(
        self,
        message: UserMessage,
        on_delta: Callable[[str], Awaitable[None]],
    ) -> AssistantResponse:
        """Same turn as handle(), but text deltas are delivered to
        on_delta as they arrive (live output for CLI/Web)."""

        return await self._run_turn(message, on_delta=on_delta)

    async def _run_turn(
        self,
        message: UserMessage,
        on_delta: Callable[[str], Awaitable[None]] | None,
    ) -> AssistantResponse:
        request_id = new_request_id()

        self.logger.info(
            "[%s] REQUEST | %s", request_id, message.content
        )

        # Persist the user turn before the first LLM call so a crash never
        # loses an accepted request.
        await self.message_store.append_user(message.content)

        history = await self.context_builder.build()

        final_content: str | None = None
        pending_approval_id: str | None = None

        # Accumulated token usage across all iterations in this turn.
        total_input_tokens = 0
        total_output_tokens = 0
        total_latency_ms = 0.0
        last_model: str | None = None

        for iteration in range(self.max_iterations):
            history = await self._compact_if_needed(history)

            await self._emit(LoopEvent(type="llm_start", iteration=iteration))

            if on_delta is not None:
                response = await self.inference_manager.stream(
                    InferenceRequest(
                        messages=history,
                        tools=self.tool_registry.openai_definitions(),
                    ),
                    requirements=ModelRequirements(tool_calling=True),
                    on_delta=on_delta,
                )
            else:
                response = await self.inference_manager.generate(
                    InferenceRequest(
                        messages=history,
                        tools=self.tool_registry.openai_definitions(),
                    ),
                    requirements=ModelRequirements(tool_calling=True),
                )

            # Accumulate usage from this LLM call.
            if response.input_tokens is not None:
                total_input_tokens += response.input_tokens
            if response.output_tokens is not None:
                total_output_tokens += response.output_tokens
            if response.latency_ms is not None:
                total_latency_ms += response.latency_ms
            if response.model:
                last_model = response.model

            # Update compaction calibration from real provider usage.
            if response.input_tokens is not None:
                estimated = estimate_history_tokens(history)
                self.compressor.update_calibration(estimated, response.input_tokens)

            if response.tool_calls:
                self.logger.info(
                    "[%s] TOOL ROUND | iteration=%s | calls=%s",
                    request_id,
                    iteration,
                    [
                        call.get("function", {}).get("name")
                        for call in response.tool_calls
                    ],
                )

                history, approval_id = await self._run_tool_round(
                    history, response.content, response.tool_calls
                )
                if approval_id:
                    pending_approval_id = approval_id
                continue

            final_content = response.content
            await self.message_store.append_assistant(final_content)
            break

        if final_content is None:
            final_content = (
                "I couldn't finish that within the iteration budget. "
                "Try narrowing the request."
            )
            if on_delta is not None:
                await on_delta(final_content)
            await self.message_store.append_assistant(final_content)

        self.logger.info(
            "[%s] RESPONSE | %s%s",
            request_id,
            final_content[:120],
            "..." if len(final_content) > 120 else "",
        )

        pending_approval = None
        if pending_approval_id:
            pending_approval = await self.approval_manager.get(pending_approval_id)

        return AssistantResponse(
            content=final_content,
            pending_approval=pending_approval,
            input_tokens=total_input_tokens or None,
            output_tokens=total_output_tokens or None,
            latency_ms=total_latency_ms or None,
            model=last_model,
        )

    async def _compact_if_needed(self, history: list[dict]) -> list[dict]:
        if self.compressor.needs_compaction(history):
            self.logger.info("compaction_triggered")
            await self._emit(LoopEvent(type="compaction"))
            history = await self.compressor.compress(history)
        return history

    async def _run_tool_round(
        self,
        history: list[dict],
        content: str | None,
        tool_calls: list[dict],
    ) -> tuple[list[dict], str | None]:
        assistant_message: dict = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        }

        await self.message_store.append_assistant(
            content or None, tool_calls=tool_calls
        )
        history = [*history, assistant_message]

        pending_approval_id: str | None = None

        for call in tool_calls:
            function = call.get("function", {})
            tool_name = function.get("name", "")

            await self._emit(
                LoopEvent(type="tool_start", tool_name=tool_name)
            )
            started = time.perf_counter()
            result, approval_id = await self._execute_call(call)
            duration_ms = (time.perf_counter() - started) * 1000
            tool_call_id = call.get("id", "unknown")

            try:
                success = bool(json.loads(result).get("success"))
                error = json.loads(result).get("error")
            except (json.JSONDecodeError, TypeError):
                success, error = False, "unparseable result"

            if approval_id:
                await self._emit(
                    LoopEvent(
                        type="approval",
                        tool_name=tool_name,
                        approval_id=approval_id,
                        resource=arguments_resource(call),
                    )
                )
            else:
                await self._emit(
                    LoopEvent(
                        type="tool_end",
                        tool_name=tool_name,
                        duration_ms=duration_ms,
                        success=success,
                        error=error,
                    )
                )

            if approval_id and pending_approval_id is None:
                pending_approval_id = approval_id

            await self.message_store.append_tool_result(tool_call_id, result)

            history = [
                *history,
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result,
                },
            ]

        return history, pending_approval_id

    async def _execute_call(self, call: dict) -> tuple[str, str | None]:
        function = call.get("function", {})
        name = function.get("name", "")
        raw_arguments = function.get("arguments", "{}")

        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else (raw_arguments or {})
            )
        except json.JSONDecodeError:
            return self._tool_error("Invalid tool arguments"), None

        try:
            tool = self.tool_registry.get(name)
        except KeyError:
            return self._tool_error(f"Unknown tool: {name}"), None

        # SAGE authority: deterministic permission check, never the LLM.
        decision = self.permission_engine.check(tool)

        # Dynamic hook: e.g. writing an existing file needs consent even
        # though the tool itself is not statically destructive.
        if (
            decision == PermissionDecision.ALLOWED
            and tool.requires_approval
            and tool.requires_approval(arguments)
        ):
            decision = PermissionDecision.APPROVAL_REQUIRED

        if decision == PermissionDecision.APPROVAL_REQUIRED:
            approval_id = await self.approval_manager.create_pending(
                tool_name=name,
                arguments=arguments,
                resource=arguments.get("path"),
            )

            self.logger.info(
                "approval_requested approval_id=%s tool=%s", approval_id, name
            )

            return (
                json.dumps(
                    {
                        "success": False,
                        "approval_required": True,
                        "approval_id": approval_id,
                        "message": (
                            "This action needs user approval. Explain what "
                            "the action does and its risk, then wait for "
                            "the user's decision."
                        ),
                    }
                ),
                approval_id,
            )

        if decision != PermissionDecision.ALLOWED:
            return self._tool_error(
                f"Permission {decision.value}: this action is blocked by "
                "policy."
            ), None

        result = await self.execution_engine.execute(
            ToolCall(tool_name=name, arguments=arguments)
        )

        if not result.success:
            return self._tool_error(result.error or "Tool failed"), None

        output = str(result.output)[: self.max_tool_result_chars]

        return json.dumps({"success": True, "output": output}), None

    @staticmethod
    def _tool_error(message: str) -> str:
        return json.dumps({"success": False, "error": message})
