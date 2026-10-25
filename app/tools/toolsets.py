"""Toolset profiles for SAGE.

Defines curated tool collections for different work modes.
Each profile specifies which tools are available.
"""

from enum import Enum
from typing import Optional
from dataclasses import dataclass, field


class ToolsetProfile(str, Enum):
    """Available toolset profiles."""
    CODING = "coding"
    RESEARCH = "research"
    WRITING = "writing"
    VERIFICATION = "verification"
    ORGANIZATION = "organization"
    FULL = "full"  # All tools (default)


@dataclass
class ToolsetConfig:
    """Configuration for a toolset profile."""
    name: str
    description: str
    tools: list[str]  # Tool names included in this profile
    employee_eligible: bool = False  # Whether employees can use this toolset


# Toolset definitions
TOOLSET_PROFILES: dict[ToolsetProfile, ToolsetConfig] = {
    ToolsetProfile.CODING: ToolsetConfig(
        name="coding",
        description="Code development, debugging, refactoring, building",
        tools=[
            "get_current_time",
            "get_current_date",
            "read_file",
            "list_files",
            "write_file",
            "patch",
            "run_command",
            "analyze",
            "grep",
            "glob",
            "session_search",
            "run_opencode",
            "run_hermes",
            "assign_task",
            "task_status",
            "await_task",
        ],
        employee_eligible=True,
    ),
    ToolsetProfile.RESEARCH: ToolsetConfig(
        name="research",
        description="Research, analysis, investigation, fact-finding",
        tools=[
            "get_current_time",
            "get_current_date",
            "read_file",
            "list_files",
            "analyze",
            "grep",
            "glob",
            "web_search",
            "browse_url",
            "session_search",
            "run_hermes",
            "assign_task",
            "task_status",
            "await_task",
        ],
        employee_eligible=True,
    ),
    ToolsetProfile.WRITING: ToolsetConfig(
        name="writing",
        description="Documentation, content creation, editing",
        tools=[
            "get_current_time",
            "get_current_date",
            "read_file",
            "list_files",
            "write_file",
            "patch",
            "analyze",
            "session_search",
            "run_hermes",
            "assign_task",
            "task_status",
            "await_task",
        ],
        employee_eligible=True,
    ),
    ToolsetProfile.VERIFICATION: ToolsetConfig(
        name="verification",
        description="Fact-checking, testing, validation",
        tools=[
            "get_current_time",
            "get_current_date",
            "read_file",
            "list_files",
            "run_command",
            "analyze",
            "grep",
            "glob",
            "session_search",
            "run_hermes",
            "assign_task",
            "task_status",
            "await_task",
        ],
        employee_eligible=True,
    ),
    ToolsetProfile.ORGANIZATION: ToolsetConfig(
        name="organization",
        description="File management, project organization, cleanup",
        tools=[
            "get_current_time",
            "get_current_date",
            "read_file",
            "list_files",
            "write_file",
            "patch",
            "run_command",
            "analyze",
            "glob",
            "session_search",
            "assign_task",
            "task_status",
            "await_task",
        ],
        employee_eligible=True,
    ),
    ToolsetProfile.FULL: ToolsetConfig(
        name="full",
        description="All available tools (default)",
        tools=[],  # Empty means all tools
        employee_eligible=False,
    ),
}


def get_toolset(profile: ToolsetProfile) -> ToolsetConfig:
    """Get toolset configuration for a profile."""
    return TOOLSET_PROFILES[profile]


def get_toolset_names() -> list[str]:
    """Get list of all toolset profile names."""
    return [p.value for p in ToolsetProfile]


def is_tool_in_toolset(tool_name: str, profile: ToolsetProfile) -> bool:
    """Check if a tool is included in a toolset profile."""
    config = TOOLSET_PROFILES[profile]
    if not config.tools:  # FULL profile has empty list = all tools
        return True
    return tool_name in config.tools


def get_active_toolset() -> ToolsetProfile:
    """Get the currently active toolset profile from environment or settings."""
    import os
    from app.config import settings
    
    # Check environment variable first
    env_profile = os.getenv("SAGE_TOOLSET_PROFILE", "").lower()
    if env_profile:
        try:
            return ToolsetProfile(env_profile)
        except ValueError:
            pass
    
    # Check settings
    if hasattr(settings, "toolset_profile") and settings.toolset_profile:
        try:
            return ToolsetProfile(settings.toolset_profile)
        except ValueError:
            pass
    
    return ToolsetProfile.FULL