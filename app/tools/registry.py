from __future__ import annotations

import builtins

from app.tools.models import ToolDefinition
from app.tools.toolsets import ToolsetProfile, is_tool_in_toolset, get_active_toolset


class ToolRegistry:
    def __init__(self, toolset_profile: ToolsetProfile | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._toolset_profile = toolset_profile or get_active_toolset()

    def set_toolset_profile(self, profile: ToolsetProfile) -> None:
        """Change the active toolset profile."""
        self._toolset_profile = profile

    def get_toolset_profile(self) -> ToolsetProfile:
        return self._toolset_profile

    def register(self, tool: ToolDefinition) -> None:
        # Check if tool is allowed in current toolset
        if not is_tool_in_toolset(tool.name, self._toolset_profile):
            return  # Silently skip tools not in active toolset
        
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")

        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError:
            pass

        # Recovery for provider junk: reasoning models sometimes leak
        # chat-template text after the real name (`assign_task` +
        # `commentary`), which sanitization may leave glued on. If
        # exactly ONE registered tool is a prefix of the requested name,
        # that is unambiguously the intended tool — it still goes through
        # every permission check like any other lookup. Ambiguity or no
        # match stays a hard error.
        matches = [tool for tool in self._tools if name.startswith(tool)]
        if len(matches) == 1:
            return self._tools[matches[0]]

        raise KeyError(f"Unknown tool: {name}") from None

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def describe(self) -> builtins.list[dict]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": (
                    tool.input_schema.model_json_schema()
                    if tool.input_schema is not None
                    else None
                ),
            }
            for tool in self._tools.values()
        ]

    def openai_definitions(self) -> list[dict]:
        """Tool definitions in OpenAI function-calling format."""

        definitions = []

        for tool in self._tools.values():
            parameters = (
                tool.input_schema.model_json_schema()
                if tool.input_schema is not None
                else {"type": "object", "properties": {}}
            )

            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": parameters,
                    },
                }
            )

        return definitions
    
    def get_available_tool_names(self) -> list[str]:
        """Get list of tool names available in current toolset."""
        return list(self._tools.keys())
