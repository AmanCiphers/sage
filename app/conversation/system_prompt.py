"""System prompt composition for the SAGE agentic conversation loop."""

from pathlib import Path

from app.config import settings
from app.memory.store import MemoryStore
from app.tools.threat_patterns import scan_for_threats

PROMPT_DIR = Path(__file__).parent


def _read_prompt_file(name: str) -> str:
    """Read a prompt component file with threat scanning."""
    path = PROMPT_DIR / name
    if path.exists():
        content = path.read_text(encoding="utf-8")
        # Scan for threats in prompt files (audit mode - log but don't block)
        threats = scan_for_threats(content, scope="context")
        if threats:
            from app.config import settings
            if settings.feature_threat_scan:
                from app.logging import get_logger
                logger = get_logger(__name__)
                logger.warning("prompt_file_threat_detected", file=name, threats=threats)
        return content
    return ""


def _build_user_context(memory_store: MemoryStore | None = None) -> str:
    """Build dynamic user context section from memory.md and user.md."""
    from app.config import settings
    parts = []

    # Long-term memory (memory.md)
    if memory_store is not None:
        memory = memory_store.load()
        if memory:
            # Scan memory for threats (audit mode)
            threats = scan_for_threats(memory, scope="context")
            if threats and settings.feature_threat_scan:
                from app.logging import get_logger
                logger = get_logger(__name__)
                logger.warning("memory_threat_detected", threats=threats)
            parts.append("## Known Facts About the User (from memory)\n" + memory)

    # User profile (user.md) - if it exists
    user_md = Path(settings.database_path).parent / "user.md"
    if user_md.exists():
        profile = user_md.read_text(encoding="utf-8").strip()
        if profile:
            # Scan user profile for threats (audit mode)
            threats = scan_for_threats(profile, scope="context")
            if threats and settings.feature_threat_scan:
                from app.logging import get_logger
                logger = get_logger(__name__)
                logger.warning("user_profile_threat_detected", threats=threats)
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
