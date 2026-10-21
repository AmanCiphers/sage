"""System prompt composition for the SAGE agentic conversation loop."""

from pathlib import Path

from app.config import settings
from app.memory.store import MemoryStore

PROMPT_DIR = Path(__file__).parent


def _read_prompt_file(name: str) -> str:
    """Read a prompt component file."""
    path = PROMPT_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _build_user_context(memory_store: MemoryStore | None = None) -> str:
    """Build dynamic user context section from memory.md and user.md."""
    parts = []

    # Long-term memory (memory.md)
    if memory_store is not None:
        memory = memory_store.load()
        if memory:
            parts.append("## Known Facts About the User (from memory)\n" + memory)

    # User profile (user.md) - if it exists
    user_md = Path(settings.database_path).parent / "user.md"
    if user_md.exists():
        profile = user_md.read_text(encoding="utf-8").strip()
        if profile:
            parts.append("## User Profile\n" + profile)

    if not parts:
        return "## User Context\n(No additional context available.)"

    return "\n\n".join(parts)


def get_system_prompt(memory_store: MemoryStore | None = None) -> str:
    """
    Compose the full system prompt from modular components.

    Order matters for attention: SOUL (character) → AUTHORITY (constraints)
    → TOOLS (guidance) → USER CONTEXT (dynamic facts).
    """
    soul = _read_prompt_file("soul.md")
    authority = _read_prompt_file("authority.md")
    tools = _read_prompt_file("tools.md")
    user_context = _build_user_context(memory_store)

    # Interpolate workspace into authority section
    authority = authority.format(workspace=settings.workspace_path.expanduser())

    sections = [
        soul,
        authority,
        tools,
        user_context,
    ]

    return "\n\n---\n\n".join(sections)
