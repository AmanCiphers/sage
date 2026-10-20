import inspect

from app.tools.registry import ToolRegistry

from .models import ExecutionResult, ToolCall


class ExecutionEngine:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(self, call: ToolCall) -> ExecutionResult:
        try:
            tool = self.registry.get(call.tool_name)

            if tool.input_schema is not None:
                validated_arguments = tool.input_schema.model_validate(
                    call.arguments
                )

                arguments = validated_arguments.model_dump()
            else:
                arguments = call.arguments

            result = tool.handler(**arguments)

            if inspect.isawaitable(result):
                result = await result

            return ExecutionResult(
                success=True,
                tool_name=call.tool_name,
                output=result,
            )

        except Exception as exc:
            return ExecutionResult(
                success=False,
                tool_name=call.tool_name,
                error=str(exc),
            )
