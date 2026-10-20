"""Deterministic shell-command classification (ArchitectureV2 §26).

The LLM never decides what is safe. A command is auto-allowed only if
its first token is on the read-only allowlist AND it contains no shell
metacharacters that could chain or redirect a second action.
"""

from pathlib import Path

READ_ONLY_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "pwd",
    "which", "echo", "date", "uname", "whoami", "df", "du", "ps",
    "file", "stat", "md5", "shasum", "basename", "dirname", "tree",
    "sort", "uniq", "diff", "cut", "tr", "less",
}

SAFE_GIT_SUBCOMMANDS = {
    "status", "log", "diff", "show", "branch", "remote", "rev-parse",
    "ls-files",
}

# Anything here can chain a second command, redirect output, or inject.
_METACHARACTERS = (";", "|", "&", "<", ">", "`", "\n", "$(")


def is_read_only_command(command: str) -> bool:
    """True only for commands that cannot cause a side effect."""

    stripped = command.strip()
    if not stripped:
        return False

    if any(marker in stripped for marker in _METACHARACTERS):
        return False

    tokens = stripped.split()
    first = Path(tokens[0]).name  # handles /usr/bin/ls

    if first in READ_ONLY_COMMANDS:
        return True

    return first == "git" and len(tokens) >= 2 and tokens[1] in SAFE_GIT_SUBCOMMANDS
