from time import perf_counter

from app.decision.interface import DecisionEngine
from app.decision.models import DecisionAction
from app.execution.engine import ExecutionEngine
from app.execution.models import ToolCall
from app.observability.logging import get_logger, new_request_id
from app.response.interface import ResponseGenerator

from .models import AssistantResponse, UserMessage


class ConversationEngine:

    def __init__(
        self,
        decision_engine: DecisionEngine,
        execution_engine: ExecutionEngine,
        response_generator: ResponseGenerator,
    ) -> None:
        self.decision_engine = decision_engine
        self.execution_engine = execution_engine
        self.logger = get_logger(__name__)
        self.response_generator = response_generator

    async def handle(
        self,
        message: UserMessage,
    ) -> AssistantResponse:

        request_id = new_request_id()
        started = perf_counter()

        self.logger.info(
            "[%s] REQUEST RECEIVED | User: %s",
            request_id,
            message.content,
        )

        self.logger.info(
            "[%s] DECISION ENGINE | Analyzing request",
            request_id,
        )

        decision = await self.decision_engine.decide(message)

        self.logger.info(
            "[%s] DECISION | Action: %s | Tool: %s",
            request_id,
            decision.action.value,
            decision.tool_name,
        )

        if decision.action == DecisionAction.RESPOND:
            response = decision.response or ""

            self.logger.info(
                "[%s] RESPONSE | %s",
                request_id,
                response,
            )

            self.logger.info(
                "[%s] REQUEST COMPLETE | %.2fs",
                request_id,
                perf_counter() - started,
            )

            return AssistantResponse(content=response)

        if decision.action == DecisionAction.USE_TOOL:
            if decision.tool_name is None:
                self.logger.warning(
                    "[%s] TOOL | No tool specified",
                    request_id,
                )

                return AssistantResponse(
                    content="I couldn't determine which tool to use.",
                )

            self.logger.info(
                "[%s] TOOL EXECUTION | %s | Arguments: %s",
                request_id,
                decision.tool_name,
                decision.arguments,
            )

            result = await self.execution_engine.execute(
                ToolCall(
                    tool_name=decision.tool_name,
                    arguments=decision.arguments,
                )
            )

            if result.success:
                self.logger.info(
                    "[%s] TOOL RESULT | %s",
                    request_id,
                    result.output,
                )

                self.logger.info(
                    "[%s] RESPONSE GENERATION | Converting tool result",
                    request_id,
                )

                response = await self.response_generator.generate(
                    message=message,
                    tool_name=decision.tool_name,
                    tool_result=result.output,
                )

                self.logger.info(
                    "[%s] RESPONSE | %s",
                    request_id,
                    response,
                )

                self.logger.info(
                    "[%s] REQUEST COMPLETE | %.2fs",
                    request_id,
                    perf_counter() - started,
                )

                return AssistantResponse(content=response)

            self.logger.error(
                "[%s] TOOL FAILED | %s",
                request_id,
                result.error,
            )

            return AssistantResponse(
                content=f"I couldn't complete that request: {result.error}",
            )

        self.logger.warning(
            "[%s] UNKNOWN DECISION",
            request_id,
        )

        return AssistantResponse(
            content="I couldn't determine what to do.",
        )



