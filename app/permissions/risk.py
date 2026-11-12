"""Single deterministic risk classifier for tool invocations.

Every tool call resolves to exactly one risk level:

- ``deny``   catastrophic command, never allowed (not even with approval)
- ``ask``    known-dangerous action, requires explicit human consent
- ``review`` ambiguous command, smart guardian decides allow vs ask
- ``allow``  ordinary operation, runs silently

The LLM never decides the risk level. The smart guardian may only refine
``review``; it can never downgrade a ``deny`` or a known-dangerous ``ask``.
"""

from __future__ import annotations

import contextlib
import re
import shlex
from pathlib import Path

from app.permissions.approval import HARDLINE_PATTERNS_COMPILED

RISK_ALLOW = "allow"
RISK_REVIEW = "review"
RISK_ASK = "ask"
RISK_DENY = "deny"

# Known-dangerous (ask) patterns. Checked AFTER the hardline blocklist.
DANGEROUS_COMMAND_PATTERNS: list[tuple[str, str]] = [
    (r"\bsudo\b", "sudo privilege escalation"),
    (r"\bdoas\b", "doas privilege escalation"),
    (r"\bsu\s", "user switching"),
    (r"\brm\s+(-[^\s]*\s+)*-?r", "recursive delete"),
    (r"\bchmod\s+777\b", "world-writable permission"),
    (r"\bchmod\s+\+x\s+/etc/", "executable system config"),
    (r"\bchown\s+root:", "change ownership to root"),
    (r">\s*/etc/", "write to system config"),
    (r"(curl|wget)\s+.*\|\s*(bash|sh|python|perl|node)\b", "download and pipe to interpreter"),
    (r"(nc|ncat|netcat)\s+.*-e\s+/bin/", "netcat with command execution"),
    (r"(ssh|scp|rsync)\s+.*root@", "SSH as root user"),
    (r"\bkill\s+-9\s+1\b", "kill init process"),
    (r"pip\s+install\s+.*--index-url\s+http://", "pip install from insecure index"),
    (r"npm\s+install\s+.*--registry\s+http://", "npm install from insecure registry"),
]

# Ambiguous constructs: ask the smart guardian before running silently.
REVIEW_COMMAND_PATTERNS: list[str] = [
    r"[;|&`]",
    r"\$\(",
    r"\bxargs\b",
    r"\bfind\b.*-exec\b",
    r"\beval\b",
    r"\bbash\s+-c\b",
    r"\bsh\s+-c\b",
    r"\bpython[0-9.]*\s+-c\b",
    r"\bosascript\b",
    r"\bperl\s+-e\b",
    r"\bruby\s+-e\b",
    r"\bnode\s+-e\b",
    r"<<-?\s*\w+",
    r"(^|\s)>\s*\S+",
]

_HARDLINE = HARDLINE_PATTERNS_COMPILED
_DANGEROUS = [(re.compile(pattern, re.IGNORECASE | re.DOTALL), desc)
               for pattern, desc in DANGEROUS_COMMAND_PATTERNS]
_REVIEW = [re.compile(pattern, re.IGNORECASE) for pattern in REVIEW_COMMAND_PATTERNS]

_SECRET_FILE_NAMES = {
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
_SECRET_DIR_PARTS = {".ssh", ".gnupg", ".aws", ".kube"}
# Directory listings reveal names but not contents, so they are not a read.
_LISTING_COMMANDS = frozenset({"ls", "ll", "la", "find", "tree", "du", "cd", "pushd", "popd"})
_SECRET_SUFFIXES = {".key", ".pem", ".crt", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}
_SYSTEM_PATH_PREFIXES = (
    "/etc",
    "/private/etc",
    "/System",
    "/Library",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/var",
)
_SHELL_RC_NAMES = {
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".gitconfig",
}


_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def _is_secret_path(token: str) -> bool:
    """True when a single argument names a secret file."""
    parts = Path(token).parts
    if not parts:
        return False

    name = parts[-1]
    if name == ".env" or name.startswith(".env."):
        # `.env.example` and friends are templates that ship in the repo.
        return not name.endswith(_TEMPLATE_SUFFIXES)
    if name in _SECRET_FILE_NAMES:
        return True
    if Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
        return True
    # The secret directory itself (``tar czf x.tar ~/.ssh``) or a path inside
    # it (``.ssh/id_rsa``, ``.aws/credentials``).
    return any(part in _SECRET_DIR_PARTS for part in parts)


def _touches_secret_file(command: str) -> bool:
    """True when the command reads a secret file.

    ``$HOME`` is expanded and every token is split into path components, so
    any reader (``less``, ``grep``, ``base64``, ``tar``…) that targets a key
    file is caught, not just the handful of verbs a regex listed. Pure
    directory listings (``ls ~/.ssh``) are not treated as reads.
    """
    expanded = command.replace("${HOME}", "~").replace("$HOME", "~")
    try:
        tokens = shlex.split(expanded)
    except ValueError:
        tokens = expanded.split()
    if not tokens:
        return False

    if Path(tokens[0]).name in _LISTING_COMMANDS:
        return False

    return any(_is_secret_path(token) for token in tokens[1:] + tokens[:1])


def _first_match(command: str, patterns, flags=re.IGNORECASE) -> str | None:
    for pattern, description in patterns:
        if pattern.search(command):
            return description
    return None


def classify_command(command: str) -> tuple[str, str]:
    """Classify a shell command.

    Returns ``(risk, reason)`` where risk is one of allow/review/ask/deny.
    """
    if not command or not isinstance(command, str) or not command.strip():
        return RISK_ASK, "empty command"

    normalized = " ".join(command.split())

    hardline = _first_match(normalized, _HARDLINE)
    if hardline:
        return RISK_DENY, f"blocked: {hardline}"

    dangerous = _first_match(normalized, _DANGEROUS)
    if dangerous:
        return RISK_ASK, dangerous

    if _touches_secret_file(normalized):
        return RISK_ASK, "credential/secret access"

    for pattern in _REVIEW:
        if pattern.search(normalized):
            return RISK_REVIEW, "ambiguous shell construct"

    return RISK_ALLOW, ""


def _trusted_roots() -> list[Path]:
    from app.tools.workspace import authorized_workspace, current_workspace

    roots = [current_workspace()]
    authorized = authorized_workspace()
    if authorized is not None:
        roots.append(authorized)
    return roots


def is_inside_trusted(path: Path) -> bool:
    """True when ``path`` is inside a directory SAGE may freely edit."""
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False

    for root in _trusted_roots():
        try:
            if resolved.is_relative_to(root.expanduser().resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def is_sensitive_path(path: Path) -> bool:
    """True for secrets, system paths, dotfiles, and data stores."""
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()

    name = resolved.name
    parts = set(resolved.parts)

    if name == ".env" or name.startswith(".env."):
        return True
    if name in _SECRET_FILE_NAMES or name in _SHELL_RC_NAMES:
        return True
    if resolved.suffix.lower() in _SECRET_SUFFIXES:
        return True
    if parts & _SECRET_DIR_PARTS:
        return True
    if name == "config" and ".git" in parts:
        return True
    if name in {"LaunchAgents", "LaunchDaemons"} and "Library" in parts:
        return True

    text = str(resolved)
    return any(
        text == prefix or text.startswith(prefix + "/")
        for prefix in _SYSTEM_PATH_PREFIXES
    )


def classify_file_write(path: str) -> tuple[str, str]:
    """Classify a write/patch to ``path``.

    Files inside an authorized/current workspace are always allowed.
    Sensitive files outside it require approval.
    """
    if not path or not isinstance(path, str):
        return RISK_ASK, "missing file path"

    target = Path(path)
    if is_inside_trusted(target):
        return RISK_ALLOW, ""
    if is_sensitive_path(target):
        return RISK_ASK, f"protected file: {target.expanduser()}"
    return RISK_ALLOW, ""


def pattern_key_for(tool_name: str, arguments: dict | None = None) -> str:
    """Stable allowlist key: tool + normalized resource."""
    arguments = arguments or {}

    if tool_name == "run_command":
        command = " ".join(str(arguments.get("command", "")).split())
        return f"run_command:{command[:200]}"

    if tool_name in ("write_file", "patch"):
        raw = arguments.get("path") or arguments.get("file") or ""
        with contextlib.suppress(OSError, ValueError):
            raw = str(Path(raw).expanduser().resolve())
        return f"{tool_name}:{raw}"

    return tool_name
