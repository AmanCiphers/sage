"""Shell command parser for capability analysis.

Parses shell commands into an AST to determine their capabilities
without relying on fragile regex matching.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import Enum

from app.tools.models import ToolCapability


class CommandType(Enum):
    SIMPLE = "simple"           # ls, cat, grep
    PIPE = "pipe"               # cmd1 | cmd2
    REDIRECT = "redirect"       # cmd > file, cmd >> file
    SUBSHELL = "subshell"       # (cmd), $(cmd), `cmd`
    SEQUENCE = "sequence"       # cmd1; cmd2, cmd1 && cmd2, cmd1 || cmd2
    BACKGROUND = "background"   # cmd &
    SUBSHELL_EXEC = "subshell_exec"  # $(cmd), `cmd`
    ASSIGNMENT = "assignment"   # VAR=value
    COMPOUND = "compound"       # { cmd1; cmd2; }


@dataclass
class CommandNode:
    type: CommandType
    command: str = ""           # For simple commands
    args: list[str] = field(default_factory=list)
    left: CommandNode | None = None
    right: CommandNode | None = None
    children: list[CommandNode] = field(default_factory=list)
    redirect_out: str | None = None
    redirect_in: str | None = None
    append: bool = False
    background: bool = False


@dataclass
class SafetyAnalysis:
    """Result of safety analysis on a command AST."""
    is_safe: bool
    capabilities: set[ToolCapability]
    risk_factors: list[str]
    reason: str = ""


# Read-only commands that never need approval
READ_ONLY_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "pwd",
    "which", "echo", "date", "uname", "whoami", "df", "du", "ps",
    "file", "stat", "md5sum", "sha1sum", "sha256sum", "basename", "dirname", "tree",
    "sort", "uniq", "diff", "cut", "tr", "less", "zcat", "zless",
    "jq", "yq", "awk", "sed", "tee", "xargs",
    "sha512sum", "zmore",
})

# Git safe subcommands
SAFE_GIT_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "branch", "remote", "rev-parse",
    "ls-files", "ls-remote", "cat-file", "verify-commit", "verify-tag",
    "describe", "blame", "shortlog", "grep", "archive",
})

# Commands that write to filesystem
WRITE_COMMANDS = frozenset({
    "touch", "mkdir", "cp", "mv", "tee", "dd", "sed", "awk",
    "tr", "sort", "uniq", "split", "csplit",
})

# Destructive commands
DESTRUCTIVE_COMMANDS = frozenset({
    "rm", "rmdir", "rm -rf", "rm -r", "shred", "wipe",
    "mkfs", "fdisk", "parted", "dd", "chmod 777", "chmod -R 777", "chown -R root",
    "format", "mkfs.",
})

# Network commands
NETWORK_COMMANDS = frozenset({
    "curl", "wget", "nc", "netcat", "ncat",
    "ssh", "scp", "rsync", "sftp", "ftp", "telnet", "nmap", "dig", "nslookup", "host",
    "ping", "traceroute",
})

# System commands
SYSTEM_COMMANDS = frozenset({
    "sudo", "systemctl", "service",
    "mount", "umount", "systemd",
    "apt", "apt-get", "aptitude", "dpkg",
    "yum", "dnf", "rpm", "pacman", "brew",
    "pip", "pip3", "npm", "yarn", "pnpm",
    "cargo", "go", "make", "cmake", "ninja",
    "docker", "podman", "kubectl", "helm",
})

# Destructive patterns
DESTRUCTIVE_PATTERNS = [
    (r"rm\s+-rf\s+/", "recursive delete of root"),
    (r"rm\s+-rf\s+~", "recursive delete of home"),
    (r"mkfs", "format filesystem"),
    (r"dd\s+.*of=/dev/", "dd to block device"),
    (r"mkfs", "format filesystem"),
    (r"fdisk", "partition disk"),
    (r"parted", "partition disk"),
    (r"shutdown", "system shutdown"),
    (r"reboot", "system reboot"),
    (r"halt", "system halt"),
    (r"poweroff", "system poweroff"),
    (r"init\s+[06]", "init 0/6"),
    (r"systemctl\s+(poweroff|reboot|halt)", "systemctl shutdown"),
    (r"kill\s+-9\s+-1", "kill all processes"),
    (r"pkill\s+-9", "pkill -9"),
    (r"killall\s+-9", "killall -9"),
]

# Metacharacters that indicate complex shell operations
METACHARACTERS = frozenset({";", "|", "&", "`", "$(", ">", "<", ">>", "<<", "&&", "||"})

# Commands that reach the network. Matched against the basename only, so a
# directory prefix cannot move a command into a different capability class.
NETWORK_COMMANDS = frozenset({
    "curl", "wget", "ssh", "scp", "rsync", "sftp", "ftp", "telnet",
    "nc", "netcat", "ncat", "nmap", "dig", "nslookup", "host", "ping",
    "traceroute",
})

# Commands that manage the host system or its package/dependency tooling.
SYSTEM_COMMANDS = frozenset({
    "systemctl", "service", "mount", "umount",
    "apt", "apt-get", "yum", "dnf", "pacman", "brew",
    "pip", "pip3", "npm", "yarn", "pnpm", "cargo", "go",
    "make", "cmake", "ninja", "docker", "podman", "kubectl", "helm",
})


def is_read_only_command(command: str) -> bool:
    """Check if a command is read-only (safe to run without approval)."""
    stripped = command.strip()
    if not stripped:
        return False

    # Check for metacharacters that indicate complex operations
    if any(mc in stripped for mc in METACHARACTERS):
        return False

    # Parse first token
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False

    if not tokens:
        return False

    first = tokens[0]
    first = first.split('/')[-1]  # Handle /usr/bin/ls

    # Check read-only commands
    if first in READ_ONLY_COMMANDS:
        return True

    # Git safe subcommands
    return first == "git" and len(tokens) > 1 and tokens[1] in SAFE_GIT_SUBCOMMANDS


def _has_destructive_patterns(command: str) -> bool:
    """Check for destructive patterns in command."""
    import re
    cmd_lower = command.lower()
    for pattern, _desc in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return True
    return False


def _has_metacharacters(command: str) -> bool:
    """Check for shell metacharacters that indicate complex operations."""
    # Only check outside of quotes
    in_single = False
    in_double = False
    for ch in command:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double and ch in METACHARACTERS:
            return True
    return False


def _resolve_run_command_capabilities(args: dict) -> list[ToolCapability]:
    """Resolve capabilities for run_command based on the command."""
    from app.tools.models import ToolCapability

    cmd = args.get("command", "").strip()
    if not cmd:
        return [ToolCapability.EXECUTE]

    if _has_destructive_patterns(cmd):
        return [ToolCapability.DESTRUCTIVE]

    # Strip any directory prefix so `/usr/bin/curl` classifies as `curl`.
    # Comparing the raw first token let a path prefix change the capability.
    first_token = cmd.split()[0].rsplit("/", 1)[-1]

    if first_token in NETWORK_COMMANDS:
        return [ToolCapability.NETWORK]

    if first_token in SYSTEM_COMMANDS:
        return [ToolCapability.SYSTEM]

    if _has_metacharacters(cmd):
        return [ToolCapability.EXECUTE]

    if is_read_only_command(cmd):
        return [ToolCapability.READ]

    return [ToolCapability.EXECUTE]
