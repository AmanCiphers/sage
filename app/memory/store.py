"""Long-term memory: a small, bounded, agent-curated facts file.

Inspired by Hermes' MEMORY.md/USER.md design: memory is not the whole chat
(raw history covers that) — it is the short list of durable facts worth
keeping in every prompt after older messages have been compacted away.

The file is markdown bullets, hard-capped in size so it can live in the
system prompt without bloating every request.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 4000


class MemoryStore:
    def __init__(self, path: Path, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self.path = path
        self.max_chars = max_chars

    def load(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8").strip()

    def entries(self) -> list[str]:
        return [line[2:].strip() for line in self.load().splitlines() if line.startswith("- ")]

    def add(self, text: str) -> str:
        """Append a fact. Duplicate facts are ignored."""

        fact = text.strip().lstrip("- ").strip()

        if not fact:
            return "Error: empty memory entry."

        existing = self.entries()

        if any(entry.lower() == fact.lower() for entry in existing):
            return "Already in memory."

        candidate = [*existing, fact]
        rendered = self._render(candidate)

        if len(rendered) > self.max_chars:
            return (
                f"Error: memory is full ({self.max_chars} chars). "
                "Remove an entry before adding this one."
            )

        self._write(rendered)
        return f"Remembered: {fact}"

    def remove(self, text: str) -> str:
        """Remove a fact by (case-insensitive) exact match or substring."""

        needle = text.strip().lower()
        existing = self.entries()

        remaining = [
            entry for entry in existing
            if needle not in entry.lower()
        ]

        if len(remaining) == len(existing):
            return f"No memory entry matches: {text}"

        self._write(self._render(remaining))
        return f"Removed {len(existing) - len(remaining)} entr{'y' if len(existing) - len(remaining) == 1 else 'ies'}."

    def clear(self) -> str:
        self._write("")
        return "Memory cleared."

    @staticmethod
    def _render(entries: list[str]) -> str:
        if not entries:
            return ""
        return "\n".join(f"- {entry}" for entry in entries) + "\n"

    def _write(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content, encoding="utf-8")
