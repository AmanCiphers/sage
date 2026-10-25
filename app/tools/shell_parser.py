"""Shell command parser for capability analysis.

Parses shell commands into an AST to determine their capabilities
without relying on fragile regex matching.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set

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
    args: List[str] = field(default_factory=list)
    left: Optional['CommandNode'] = None
    right: Optional['CommandNode'] = None
    children: List['CommandNode'] = field(default_factory=list)
    redirect_out: Optional[str] = None
    redirect_in: Optional[str] = None
    append: bool = False
    background: bool = False


# Read-only commands that never need approval
READ_ONLY_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "pwd",
    "which", "echo", "date", "uname", "whoami", "df", "du", "ps",
    "file", "stat", "md5sum", "sha1sum", "sha256sum", "basename", "dirname", "tree",
    "sort", "uniq", "diff", "cut", "tr", "less", "zcat", "zless",
    "jq", "yq", "awk", "sed", "sort", "uniq", "tee", "xargs",
    "file", "stat", "md5sum", "sha1sum", "sha256sum", "sha512sum",
    "zcat", "zless", "zmore",
})

# Git safe subcommands
SAFE_GIT_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "branch", "remote", "rev-parse",
    "ls-files", "ls-remote", "cat-file", "verify-commit", "verify-tag",
    "describe", "blame", "shortlog", "grep", "archive",
})

# Commands that write to filesystem
WRITE_COMMANDS = frozenset({
    "touch", "mkdir", "cp", "mv", "cp", "tee", "dd", "sed", "awk",
    "tr", "sort", "uniq", "split", "csplit",
})

# Destructive commands
DESTRUCTIVE_COMMANDS = frozenset({
    "rm", "rmdir", "rm -rf", "rm -r", "shred", "wipe",
    "mkfs", "fdisk", "parted", "dd", "shred",
    "chmod 777", "chmod -R 777", "chown -R root",
    "mkfs", "fdisk", "dd", "shred",
    "format", "mkfs.", "fdisk", "parted",
})

# Network commands
NETWORK_COMMANDS = frozenset({
    "curl", "wget", "wget", "curl", "nc", "netcat", "ncat",
    "ssh", "scp", "rsync", "sftp", "ftp", "sftp",
    "telnet", "nc", "nmap", "dig", "nslookup", "host",
    "ping", "traceroute", "curl", "wget",
})

# System commands
SYSTEM_COMMANDS = frozenset({
    "sudo", "systemctl", "systemctl", "service",
    "mount", "umount", "mount", "umount",
    "systemctl", "service", "systemd",
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
    if first == "git" and len(tokens) > 1 and tokens[1] in {
        "status", "log", "diff", "show", "branch", "remote", "rev-parse",
        "ls-files", "ls-remote", "cat-file", "verify-commit", "verify-tag",
        "describe", "blame", "shortlog", "grep", "archive",
    }:
        return True

    return False


def _has_destructive_patterns(command: str) -> bool:
    """Check for destructive patterns in command."""
    cmd_lower = command.lower()
    for pattern, desc in DESTRUCTIVE_PATTERNS:
        import re
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return True
    return False


def _has_metacharacters(command: str) -> bool:
    """Check for shell metacharacters that indicate complex operations."""
    # Only check outside of quotes
    in_single = False
    in_double = False
    for i, ch in enumerate(command):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double and ch in METACHARACTERS:
            return True
    return False


def _resolve_run_command_capabilities(args: dict) -> list:
    """Resolve capabilities for run_command based on the command."""
    from app.tools.models import ToolCapability
    
    cmd = args.get("command", "").strip()
    if not cmd:
        return ["execute"]
    
    # Check for destructive patterns
    if _has_destructive_patterns(cmd):
        return ["destructive"]
    
    # Check for network commands
    first_token = cmd.split()[0] if cmd.strip() else ""
    if first_token in {"curl", "wget", "ssh", "scp", "rsync", "nc", "netcat", "ssh", "scp", "sftp", "ftp", "telnet", "ssh", "dig", "nslookup", "host", "ping", "traceroute"}:
        return ["network"]
    
    # Check for system commands
    first_token = cmd.split()[0].split('/')[-1] if cmd.strip() else ""
    if first_token in {"systemctl", "service", "systemctl", "service", "systemctl", "mount", "umount", "apt", "apt-get", "apt", "yum", "dnf", "pacman", "brew", "pip", "pip3", "npm", "yarn", "pnpm", "cargo", "go", "make", "cmake", "ninja", "docker", "podman", "kubectl", "helm"}:
        return ["system"]
    
    # Check for network commands
    if first_token in {"curl", "wget", "ssh", "scp", "rsync", "nc", "netcat", "ncat", "ssh", "scp", "rsync", "nc", "nmap", "dig", "nslookup", "host", "ping", "traceroute"}:
        return ["network"]
    
    # Check for destructive patterns
    if _has_destructive_patterns(cmd):
        return ["destructive"]
    
    # Check for metacharacters
    if _has_metacharacters(cmd):
        return ["execute"]
    
    # Check if read-only
    if is_read_only_command(cmd):
        return ["read"]
    
    return ["execute"]


def _has_destructive_patterns(command: str) -> bool:
    """Check for destructive patterns."""
    import re
    cmd_lower = command.lower()
    for pattern, desc in DESTRUCTIVE_PATTERNS:
        import re
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return True
    return False